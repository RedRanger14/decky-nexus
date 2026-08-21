using System;
using System.Collections.Generic;
using System.IO;
using Frosty.ModSupport.Archive;
using Frosty.ModSupport.ModEntries;
using Frosty.ModSupport.ModInfos;
using Frosty.Sdk;
using Frosty.Sdk.DbObjectElements;
using Frosty.Sdk.Interfaces;
using Frosty.Sdk.IO;
using Frosty.Sdk.Managers;
using Frosty.Sdk.Managers;
using Frosty.Sdk.Managers.CatResources;
using Frosty.Sdk.Managers.Entries;
using Frosty.Sdk.Managers.Infos;
using Frosty.Sdk.Managers.Infos.FileInfos;
using Frosty.Sdk.Managers.Infos.FileInfos.ResourceInfo;
using Frosty.Sdk.Utils;

namespace Frosty.ModSupport;

public partial class FrostyModExecutor
{
    /// <summary>
    /// SuperBundleManifest (Star Wars Battlefront II 2017, Battlefield V pre-2019 layouts):
    /// the game has no per-SuperBundle toc/sb files. Instead layout.toc's "manifest" entry
    /// points at one global blob inside a cas file:
    ///
    ///   u32 resourceInfoCount, u32 bundleCount, u32 chunkCount
    ///   resourceInfos[]: { u32 manifestFileIdentifier, u32 offset, i64 size }
    ///   bundles[]:       { i32 nameHash, i32 startIndex, i32 resourceCount, u64 zero }
    ///   chunks[]:        { guid id, i32 resourceInfoIndex }
    ///
    /// A bundle's resourceInfo range is [startIndex, startIndex+resourceCount): the first
    /// entry is the BinaryBundle meta, the rest are its assets in bundle order (the same
    /// order BinaryBundle.Modify walks, which is why its callback index maps to
    /// range[i + 1] - identical to Manifest2019's non-inline branch).
    ///
    /// Because the manifest is global rather than per-SuperBundle, this runs ONCE after
    /// all cas archives are written (offsets must be resolvable via GetFileInfo), not in
    /// the per-SuperBundleInstallChunk loop.
    /// </summary>
    // (installChunk, isPatch, casIndex) -> that cas file's resource entries,
    // ordered by offset, read from the game's own cas.cat files.
    //
    // Needed because the manifest's per-bundle ranges are COALESCED: one entry
    // can cover several physical cas resources. Frosty v1 expands them against
    // the catalog, and parsing v1's output confirms the shape - a bundle whose
    // original range holds 71 entries becomes 291, exactly one per asset plus
    // the meta.
    private readonly Dictionary<(uint, bool, int), List<(uint Offset, uint Size, Sha1 Sha1)>>
        m_casEntryCache = new();

    private List<(uint Offset, uint Size, Sha1 Sha1)> GetCasEntries(CasFileIdentifier inId)
    {
        (uint, bool, int) key = (inId.InstallChunkIndex, inId.IsPatch, inId.CasIndex);
        if (m_casEntryCache.TryGetValue(key, out List<(uint, uint, Sha1)>? cached))
        {
            return cached;
        }

        List<(uint Offset, uint Size, Sha1 Sha1)> entries = new();
        InstallChunkInfo ic = FileSystemManager.GetInstallChunkInfo(inId.InstallChunkIndex);
        FileSystemSource source = inId.IsPatch ? FileSystemSource.Patch : FileSystemSource.Base;

        if (source.TryResolvePath(Path.Combine(ic.InstallBundle, "cas.cat"),
                out string? catPath))
        {
            using (CatStream stream = new(catPath))
            {
                for (int i = 0; i < stream.ResourceCount; i++)
                {
                    CatResourceEntry e = stream.ReadResourceEntry();
                    if (e.ArchiveIndex == inId.CasIndex)
                    {
                        entries.Add((e.Offset, e.Size, e.Sha1));
                    }
                }

                for (int i = 0; i < stream.EncryptedCount; i++)
                {
                    CatResourceEntry e = stream.ReadEncryptedEntry();
                    if (e.ArchiveIndex == inId.CasIndex)
                    {
                        entries.Add((e.Offset, e.Size, e.Sha1));
                    }
                }
            }
        }

        entries.Sort((a, b) => a.Offset.CompareTo(b.Offset));
        m_casEntryCache.Add(key, entries);
        return entries;
    }

    /// <summary>
    /// One coalesced manifest entry -> the physical cas resources it covers.
    /// When the catalog cannot account for the range exactly the original
    /// entry is kept, so a partial failure stays local instead of writing a
    /// wrong expansion.
    /// </summary>
    private List<(CasFileIdentifier Id, uint Offset, long Size, Sha1 Sha1)> ExpandEntry(
        CasFileIdentifier inId, uint inOffset, long inSize)
    {
        List<(CasFileIdentifier, uint, long, Sha1)> result = new();
        List<(uint Offset, uint Size, Sha1 Sha1)> entries = GetCasEntries(inId);

        int start = entries.FindIndex(e => e.Offset == inOffset);
        if (start < 0)
        {
            result.Add((inId, inOffset, inSize, Sha1.Zero));
            return result;
        }

        long covered = 0;
        for (int i = start; i < entries.Count && covered < inSize; i++)
        {
            result.Add((inId, entries[i].Offset, entries[i].Size, entries[i].Sha1));
            covered += entries[i].Size;
        }

        if (covered != inSize)
        {
            result.Clear();
            result.Add((inId, inOffset, inSize, Sha1.Zero));
        }

        return result;
    }

    private void ModSuperBundleManifest(string inModPackPath)
    {
        DbObjectDict manifestDict = FileSystemManager.SuperBundleManifest!;

        CasFileIdentifier manifestFile =
            CasFileIdentifier.FromManifestFileIdentifier(manifestDict.AsUInt("file"));

        // 1. Parse the original manifest, exactly as ManifestAssetLoader reads it.
        List<(CasFileIdentifier Id, uint Offset, long Size)> files = new();
        List<(int NameHash, int StartIndex, int ResourceCount)> bundles = new();
        List<(Guid Id, int Index)> chunks = new();

        using (BlockStream stream = BlockStream.FromFile(FileSystemManager.GetFilePath(manifestFile),
                   manifestDict.AsUInt("offset"), manifestDict.AsInt("size")))
        {
            uint resourceInfoCount = stream.ReadUInt32();
            uint bundleCount = stream.ReadUInt32();
            uint chunkCount = stream.ReadUInt32();

            for (int i = 0; i < resourceInfoCount; i++)
            {
                files.Add((CasFileIdentifier.FromManifestFileIdentifier(stream.ReadUInt32()),
                    stream.ReadUInt32(), stream.ReadInt64()));
            }

            for (int i = 0; i < bundleCount; i++)
            {
                int nameHash = stream.ReadInt32();
                int startIndex = stream.ReadInt32();
                int resourceCount = stream.ReadInt32();
                stream.Position += sizeof(ulong);
                bundles.Add((nameHash, startIndex, resourceCount));
            }

            for (int i = 0; i < chunkCount; i++)
            {
                chunks.Add((stream.ReadGuid(), stream.ReadInt32()));
            }
        }

        // 2. Merge every SuperBundle's mod info: the manifest is global, so per-sbIc
        // splits collapse back into one view. Bundle keys are AssetManager bundle ids
        // (HashString(Name + Parent.Name)); the manifest speaks nameHash
        // (HashString(Name)) - map via the BundleInfo, with the loader's hex fallback
        // for bundles whose real name never resolved.
        Dictionary<int, BundleModInfo> modifiedByNameHash = new();
        Dictionary<Guid, ChunkModEntry> chunkMods = new();
        HashSet<Guid> removedChunks = new();
        List<(Guid Id, SuperBundleInstallChunk SbIc)> addedChunks = new();

        foreach (KeyValuePair<int, SuperBundleModInfo> sb in m_superBundleModInfos)
        {
            SuperBundleInstallChunk sbIc = FileSystemManager.GetSuperBundleInstallChunk(sb.Key);

            if (sb.Value.Added.Bundles.Count > 0)
            {
                // Adding whole new bundles to the manifest is possible (v1 did it) but
                // not needed by asset-replacement mods; failing loudly beats writing a
                // manifest that silently lacks them.
                throw new NotImplementedException(
                    "SuperBundleManifest: adding new bundles is not supported yet");
            }

            foreach (KeyValuePair<int, BundleModInfo> bundle in sb.Value.Modified.Bundles)
            {
                BundleInfo? info = AssetManager.GetBundleInfo(bundle.Key);
                if (info is null)
                {
                    throw new Exception(
                        $"SuperBundleManifest: unknown modified bundle {bundle.Key:X8}");
                }

                int nameHash;
                if (info.Name.Length == 8
                    && int.TryParse(info.Name, System.Globalization.NumberStyles.HexNumber,
                        null, out int parsed))
                {
                    // The loader names unresolved bundles by their hash ("X8").
                    nameHash = parsed;
                }
                else
                {
                    nameHash = Sdk.Utils.Utils.HashString(info.Name, true);
                }

                modifiedByNameHash[nameHash] = bundle.Value;
            }

            foreach (Guid id in sb.Value.Modified.Chunks)
            {
                chunkMods[id] = m_modifiedChunks[id];
            }

            foreach (Guid id in sb.Value.Removed.Chunks)
            {
                removedChunks.Add(id);
            }

            foreach (Guid id in sb.Value.Added.Chunks)
            {
                chunkMods[id] = m_modifiedChunks[id];
                addedChunks.Add((id, sbIc));
            }
        }

        // 3. Rebuild the tables. Ranges shift as modified bundles gain entries, so the
        // files table is rebuilt from scratch in original bundle order.
        List<(CasFileIdentifier Id, uint Offset, long Size)> newFiles = new(files.Count);
        List<(int NameHash, int StartIndex, int ResourceCount)> newBundles = new(bundles.Count);

        foreach ((int NameHash, int StartIndex, int ResourceCount) bundle in bundles)
        {
            int newStart = newFiles.Count;

            if (!modifiedByNameHash.TryGetValue(bundle.NameHash, out BundleModInfo? modInfo))
            {
                for (int i = 0; i < bundle.ResourceCount; i++)
                {
                    newFiles.Add(files[bundle.StartIndex + i]);
                }
                newBundles.Add((bundle.NameHash, newStart, bundle.ResourceCount));
                continue;
            }

            // The writer for this bundle's install chunk: same resolution the loader
            // uses (first superbundle of the meta's install chunk).
            (CasFileIdentifier Id, uint Offset, long Size) meta = files[bundle.StartIndex];
            InstallChunkInfo ic = FileSystemManager.GetInstallChunkInfo(meta.Id.InstallChunkIndex);
            string superBundle = string.Empty;
            foreach (string sbName in ic.SuperBundles)
            {
                superBundle = sbName;
                break;
            }
            SuperBundleInstallChunk sbIc = FileSystemManager.GetSuperBundleInstallChunk(superBundle);
            InstallChunkWriter writer = GetInstallChunkWriter(sbIc);

            // The meta must still be rebuilt: its sha1s and sizes change.
            Block<byte> bundleMeta;
            using (BlockStream bundleStream = BlockStream.FromFile(
                       FileSystemManager.GetFilePath(meta.Id), meta.Offset, (int)meta.Size))
            {
                bundleMeta = BinaryBundle.Modify(bundleStream, modInfo, m_modifiedEbx,
                    m_modifiedRes, m_modifiedChunks, (_, _, _, _, _) => { });
            }

            // Build the range in META ORDER, one entry per asset.
            //
            // v1's output has exactly 1 + assetCount entries per bundle, and
            // the game evidently relies on that alignment: entry i + 1
            // describes the meta's asset i. Expanding the original range gives
            // the right entries for assets that were already there, but a mod
            // that ADDS assets shifts everything after them, so the list has
            // to be assembled from the rebuilt meta rather than patched in
            // place. Locations are looked up per asset:
            //
            //   1. data we wrote this run (modified, or added with new data)
            //   2. the expanded original entry for that sha1 (unmodified)
            //   3. the base catalog location
            //   4. a zero placeholder, to keep the alignment honest
            Dictionary<Sha1, (CasFileIdentifier, uint, long)> expanded = new();
            int expandedCount = 0;
            for (int i = 1; i < bundle.ResourceCount; i++)
            {
                (CasFileIdentifier Id, uint Offset, long Size) original =
                    files[bundle.StartIndex + i];

                foreach ((CasFileIdentifier Id, uint Offset, long Size, Sha1 Sha1) part
                         in ExpandEntry(original.Id, original.Offset, original.Size))
                {
                    expandedCount++;
                    if (part.Sha1 != Sha1.Zero)
                    {
                        expanded[part.Sha1] = (part.Id, part.Offset, part.Size);
                    }
                }
            }

            List<(CasFileIdentifier, uint, long)> range = new(expandedCount + 64);

            Sha1 metaSha1 = Sdk.Utils.Utils.GenerateSha1(bundleMeta.ToSpan());
            (CasFileIdentifier File, uint Offset, uint Size) written =
                writer.WriteData(metaSha1, bundleMeta);
            range.Add((written.File, written.Offset, written.Size));

            // The rebuilt meta IS the order the game will read.
            Frosty.Sdk.IO.BinaryBundle rebuilt;
            using (BlockStream metaStream = new(bundleMeta, false))
            {
                rebuilt = Frosty.Sdk.IO.BinaryBundle.Deserialize(metaStream);
            }
            bundleMeta.Dispose();

            int fromWritten = 0, fromExpanded = 0, fromCatalog = 0, placeholders = 0;

            // inChanged says whether the MOD actually changes this asset.
            //
            // It matters because the executor also writes copies of base-game
            // data for assets a mod merely ADDS to a bundle, and that copy is
            // not always faithful: assets stored as base+delta patch entries
            // lose the merge and come out as a small blob that inflates into
            // nonsense. Two hero VO assets failed exactly that way. So for an
            // unchanged asset the ORIGINAL location wins, and our written copy
            // is only used when the mod genuinely replaced the content.
            void AddEntry(Sha1 inSha1, uint inRangeStart, uint inRangeEnd, int inFirstMip,
                bool inChanged)
            {
                InstallChunkWriter? holder = null;
                if (inChanged)
                {
                    if (writer.HasData(inSha1))
                    {
                        holder = writer;
                    }
                    else
                    {
                        // Data is written per SUPERBUNDLE, so an asset added to
                        // a bundle in one install chunk may have been written
                        // through another chunk's writer.
                        foreach (InstallChunkWriter candidate in m_installChunkWriters.Values)
                        {
                            if (candidate.HasData(inSha1))
                            {
                                holder = candidate;
                                break;
                            }
                        }
                    }
                }

                if (holder is not null)
                {
                    (CasFileIdentifier File, uint Offset, uint Size) info =
                        holder.GetFileInfo(inSha1);
                    if (inFirstMip > 0)
                    {
                        info.Offset += inRangeStart;
                        info.Size = inRangeEnd - inRangeStart;
                    }
                    range.Add((info.File, info.Offset, info.Size));
                    fromWritten++;
                    return;
                }

                if (expanded.TryGetValue(inSha1, out (CasFileIdentifier, uint, long) hit))
                {
                    range.Add(hit);
                    fromExpanded++;
                    return;
                }

                CasResourceInfo? baseInfo = ResourceManager.GetFileInfo(inSha1)?.GetBase();
                if (baseInfo is not null)
                {
                    range.Add((baseInfo.GetIdentifier(), baseInfo.GetFileOffset(),
                        baseInfo.GetSize()));
                    fromCatalog++;
                    return;
                }

                // Unchanged and unlocatable: fall back to whatever was written
                // for it rather than a dead entry.
                foreach (InstallChunkWriter candidate in m_installChunkWriters.Values)
                {
                    if (candidate.HasData(inSha1))
                    {
                        (CasFileIdentifier File, uint Offset, uint Size) info =
                            candidate.GetFileInfo(inSha1);
                        range.Add((info.File, info.Offset, info.Size));
                        fromWritten++;
                        return;
                    }
                }

                range.Add((default, 0u, 0L));
                placeholders++;
            }

            foreach (EbxAssetEntry e in rebuilt.EbxList)
            {
                AddEntry(e.Sha1, 0, 0, -1,
                    modInfo.Modified.Ebx.Contains(e.Name) || modInfo.Added.Ebx.Contains(e.Name));
            }

            foreach (ResAssetEntry r in rebuilt.ResList)
            {
                AddEntry(r.Sha1, 0, 0, -1,
                    modInfo.Modified.Res.Contains(r.Name) || modInfo.Added.Res.Contains(r.Name));
            }

            foreach (ChunkAssetEntry c in rebuilt.ChunkList)
            {
                uint rs = 0, re = 0;
                int fm = -1;
                if (m_modifiedChunks.TryGetValue(c.Id, out ChunkModEntry? cm))
                {
                    rs = cm.RangeStart;
                    re = cm.RangeEnd;
                    fm = cm.FirstMip;
                }
                AddEntry(c.Sha1, rs, re, fm,
                    modInfo.Modified.Chunks.Contains(c.Id) || modInfo.Added.Chunks.Contains(c.Id));
            }

            Frosty.Sdk.FrostyLogger.Logger?.LogInfo(
                $"RANGEBUILD bundle={bundle.NameHash:X8} orig={bundle.ResourceCount} " +
                $"expanded={expandedCount} built={range.Count} " +
                $"assets={rebuilt.EbxList.Length + rebuilt.ResList.Length + rebuilt.ChunkList.Length} " +
                $"written={fromWritten} reused={fromExpanded} catalog={fromCatalog} " +
                $"placeholder={placeholders}");

            newFiles.AddRange(range);
            newBundles.Add((bundle.NameHash, newStart, range.Count));
        }

        // Chunks: same rebuild, indices re-point at the new files table.
        List<(Guid Id, int Index)> newChunks = new(chunks.Count);
        foreach ((Guid Id, int Index) chunk in chunks)
        {
            if (removedChunks.Contains(chunk.Id))
            {
                continue;
            }

            if (chunkMods.TryGetValue(chunk.Id, out ChunkModEntry? mod))
            {
                (CasFileIdentifier Id, uint Offset, long Size) orig = files[chunk.Index];
                InstallChunkInfo ic = FileSystemManager.GetInstallChunkInfo(orig.Id.InstallChunkIndex);
                string superBundle = string.Empty;
                foreach (string sbName in ic.SuperBundles)
                {
                    superBundle = sbName;
                    break;
                }
                InstallChunkWriter writer =
                    GetInstallChunkWriter(FileSystemManager.GetSuperBundleInstallChunk(superBundle));

                (CasFileIdentifier File, uint Offset, uint Size) info = writer.GetFileInfo(mod.Sha1);
                if (mod.FirstMip > 0)
                {
                    info.Offset += mod.RangeStart;
                    info.Size = mod.RangeEnd - mod.RangeStart;
                }

                newChunks.Add((chunk.Id, newFiles.Count));
                newFiles.Add((info.File, info.Offset, info.Size));
            }
            else
            {
                newChunks.Add((chunk.Id, newFiles.Count));
                newFiles.Add(files[chunk.Index]);
            }
        }

        foreach ((Guid Id, SuperBundleInstallChunk SbIc) added in addedChunks)
        {
            ChunkModEntry mod = chunkMods[added.Id];
            InstallChunkWriter writer = GetInstallChunkWriter(added.SbIc);

            (CasFileIdentifier File, uint Offset, uint Size) info = writer.GetFileInfo(mod.Sha1);
            if (mod.FirstMip > 0)
            {
                info.Offset += mod.RangeStart;
                info.Size = mod.RangeEnd - mod.RangeStart;
            }

            newChunks.Add((added.Id, newFiles.Count));
            newFiles.Add((info.File, info.Offset, info.Size));
        }

        // 4. Serialize the new manifest blob (little endian, like the loader reads it).
        Block<byte> blob = new(0);
        using (BlockStream ws = new(blob, true))
        {
            ws.WriteUInt32((uint)newFiles.Count);
            ws.WriteUInt32((uint)newBundles.Count);
            ws.WriteUInt32((uint)newChunks.Count);

            foreach ((CasFileIdentifier Id, uint Offset, long Size) file in newFiles)
            {
                ws.WriteUInt32(CasFileIdentifier.ToManifestFileIdentifier(file.Id));
                ws.WriteUInt32(file.Offset);
                ws.WriteInt64(file.Size);
            }

            foreach ((int NameHash, int StartIndex, int ResourceCount) bundle in newBundles)
            {
                ws.WriteInt32(bundle.NameHash);
                ws.WriteInt32(bundle.StartIndex);
                ws.WriteInt32(bundle.ResourceCount);
                ws.WriteUInt64(0);
            }

            foreach ((Guid Id, int Index) chunk in newChunks)
            {
                ws.WriteGuid(chunk.Id);
                ws.WriteInt32(chunk.Index);
            }
        }

        // 5. The blob itself lives in a cas: write it through the manifest's own
        // install chunk so the catalog covers it (v1 parity).
        InstallChunkInfo manifestIc = FileSystemManager.GetInstallChunkInfo(manifestFile.InstallChunkIndex);
        string manifestSb = string.Empty;
        foreach (string sbName in manifestIc.SuperBundles)
        {
            manifestSb = sbName;
            break;
        }
        InstallChunkWriter manifestWriter =
            GetInstallChunkWriter(FileSystemManager.GetSuperBundleInstallChunk(manifestSb));

        Sha1 blobSha1 = Sdk.Utils.Utils.GenerateSha1(blob.ToSpan());
        (CasFileIdentifier File, uint Offset, uint Size) manifestWritten =
            manifestWriter.WriteData(blobSha1, blob);
        blob.Dispose();

        // 6. layout.toc: point the manifest entry at the rebuilt blob. Everything else
        // in the layout is preserved; the untouched-file symlink pass skips this file
        // because it now exists in ModData.
        // SWBF2 keeps layout.toc in Data/, not Patch/ - resolve it rather than
        // assume, and mirror it at the same relative location under ModData so
        // the game (launched with -dataPath) finds ours first. The source
        // symlink pass must then link Data's remaining files individually
        // instead of symlinking the whole directory over ours.
        // ResolvePath builds the path without checking the disk, and SWBF2 has
        // no Patch/layout.toc - the real one lives in the base source (Data/).
        string layoutPath = FileSystemManager.ResolvePath(true, "layout.toc");
        if (!File.Exists(layoutPath))
        {
            layoutPath = FileSystemManager.ResolvePath(false, "layout.toc");
        }
        if (!File.Exists(layoutPath))
        {
            throw new Exception("SuperBundleManifest: no layout.toc found");
        }
        string relative = Path.GetRelativePath(FileSystemManager.BasePath, layoutPath);

        DbObjectDict layout = DbObject.Deserialize(layoutPath)!.AsDict();
        DbObjectDict layoutManifest = layout.AsDict("manifest");
        layoutManifest.Set("file", CasFileIdentifier.ToManifestFileIdentifier(manifestWritten.File));
        layoutManifest.Set("offset", manifestWritten.Offset);
        layoutManifest.Set("size", (int)manifestWritten.Size);
        layoutManifest.Set("sha1", blobSha1);

        // The PACK ROOT, not m_modDataPath: that is ModData/<pack>/<patchPath>,
        // so writing "Data/layout.toc" under it produced Patch/Data/layout.toc
        // while the real Data/layout.toc stayed a symlink to the original -
        // the engine then read original manifest offsets against our rebuilt
        // cas files and crashed. v1 wrote to the pack root for this game too.
        string outPath = Path.Combine(inModPackPath, relative);
        Directory.CreateDirectory(Directory.GetParent(outPath)!.FullName);
        using (DataStream stream = new(File.Create(outPath)))
        {
            ObfuscationHeader.Write(stream);
            DbObject.Serialize(stream, layout);
        }
    }
}
