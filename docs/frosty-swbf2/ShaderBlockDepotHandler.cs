using System;
using System.Collections.Generic;
using Frosty.ModSupport.Attributes;
using Frosty.ModSupport.Interfaces;
using Frosty.ModSupport.ModEntries;
using Frosty.Sdk;
using Frosty.Sdk.IO;
using Frosty.Sdk.Managers;
using Frosty.Sdk.Managers.Entries;
using Frosty.Sdk.Utils;

namespace Frosty.ModSupport.Handlers;

/// <summary>
/// Star Wars Battlefront II ShaderBlockDepot merge handler, ported from
/// Frosty v1's MeshSetPlugin (ShaderBlockDepotCustomActionHandler and
/// ShaderBlockDepot.cs, hash 0x89EF2205).
///
/// A mesh mod does not ship a whole ShaderBlockDepot: it ships a DELTA -
/// only the shader blocks it changes, keyed by hash - and expects the mod
/// manager to merge them into the game's own depot at apply time. With no
/// handler registered the executor silently skips the resource, so the game
/// kept its ORIGINAL shader blocks while the mesh they describe was
/// replaced, and every replaced character rendered as a mass of shards.
/// Battle Damaged Darth Vader was the mod that exposed it.
///
/// The wire formats below are byte-for-byte the v1 ones, verified by
/// diffing our output against a pack built by real Frosty v1 for the same
/// mod. Only what a MERGE needs is ported: parameters are carried as opaque
/// records, never rehashed or edited.
/// </summary>
[Handler(unchecked((int)0x89EF2205))]
public class ShaderBlockDepotHandler : IHandler
{
    private ModifiedShaderBlockDepot? m_merged;

    public void Load(Block<byte> inData)
    {
        ModifiedShaderBlockDepot delta;
        using (BlockStream stream = new(inData, false))
        {
            // v1 ModifiedResource framing: assembly-qualified type name,
            // then the payload raw to the end - no size prefix.
            string type = stream.ReadNullTerminatedString();
            if (!type.Contains("ModifiedShaderBlockDepot", StringComparison.OrdinalIgnoreCase))
            {
                throw new Exception($"Expected a ModifiedShaderBlockDepot delta, got '{type}'");
            }

            Block<byte> payload = new((int)(stream.Length - stream.Position));
            stream.ReadExactly(payload);
            try
            {
                delta = new ModifiedShaderBlockDepot(payload);
            }
            finally
            {
                payload.Dispose();
            }
        }

        if (m_merged is null)
        {
            m_merged = delta;
        }
        else
        {
            // Later mods win, same as v1: existing entries are replaced,
            // new ones appended.
            m_merged.Merge(delta);
        }
    }

    public void Modify(IModEntry modEntry, out Block<byte> data)
    {
        ResModEntry entry = (ResModEntry)modEntry;
        ResAssetEntry? resEntry = AssetManager.GetResAssetEntry(entry.Name);
        if (resEntry is null || m_merged is null)
        {
            throw new Exception($"No game ShaderBlockDepot to merge '{entry.Name}' into");
        }

        byte[] meta = (byte[])resEntry.ResMeta.Clone();
        ShaderBlockDepot depot;
        using (Block<byte> raw = AssetManager.GetAsset(resEntry))
        {
            depot = new ShaderBlockDepot(raw, meta, m_merged);
        }

        using Block<byte> newRaw = depot.ToBytes(meta);

        // The entry the bundle metas and the manifest will describe: the
        // merged result's sizes and meta, under the delta's sha1 - which is
        // also the key the merged bytes are stored under, so the pack stays
        // self-consistent. The game never verifies sha1s against content
        // (proven on device); what it does trust are these two fields.
        entry.OriginalSize = newRaw.Size;
        entry.ResMeta = meta;

        data = Cas.CompressData(newRaw, ProfilesLibrary.ResCompression, 0);
    }
}

/// <summary>One shader block record, common header: u64 hash.</summary>
internal class ShaderBlockResource
{
    public int Index;
    public ulong Hash;

    public virtual void Read(DataStream reader, List<ShaderBlockResource>? shaderBlockEntries)
    {
        Hash = reader.ReadUInt64();
    }

    internal virtual void Save(DataStream writer, List<int> relocTable, out long startOffset)
    {
        startOffset = writer.Position;
        writer.WriteUInt64(Hash);
        relocTable.Add((int)writer.Position);
    }
}

/// <summary>An opaque shader parameter. Read and written verbatim - a merge
/// never edits parameters, so none of v1's typed accessors are ported.</summary>
internal class ParameterEntry
{
    private ulong m_parameterHash;
    private uint m_typeHash;
    private ushort m_used;
    private ushort m_nameHi;
    private byte[] m_value = Array.Empty<byte>();

    public ParameterEntry(DataStream reader)
    {
        m_parameterHash = reader.ReadUInt64();
        m_typeHash = reader.ReadUInt32();
        m_used = reader.ReadUInt16();
        m_nameHi = reader.ReadUInt16();

        int size = reader.ReadInt32();
        if (m_typeHash == 0xad0abfd3 /* ITexture: file says 1, data is 16 */)
        {
            size = 16;
        }
        m_value = reader.ReadBytes(size);
    }

    public void Write(DataStream writer)
    {
        writer.WriteUInt64(m_parameterHash);
        writer.WriteUInt32(m_typeHash);
        writer.WriteUInt16(m_used);
        writer.WriteUInt16(m_nameHi);
        writer.WriteInt32(m_typeHash == 0xad0abfd3 ? 1 : m_value.Length);
        writer.Write(m_value);
    }
}

internal class ShaderStaticParamDbBlock : ShaderBlockResource
{
    public List<ShaderBlockResource> Resources = new();

    public override void Read(DataStream reader, List<ShaderBlockResource>? shaderBlockEntries)
    {
        base.Read(reader, shaderBlockEntries);

        long offset = reader.ReadInt64();
        long size = reader.ReadInt64();

        reader.Position = offset;
        for (long i = 0; i < size; i++)
        {
            int index = reader.ReadInt32();
            if (shaderBlockEntries is null || index >= shaderBlockEntries.Count)
            {
                // v1 passed null here for deltas and would have thrown, so
                // no working mod hits this - but say something usable if a
                // broken one does.
                throw new Exception("A shader block delta references a resource outside itself");
            }
            Resources.Add(shaderBlockEntries[index]);
        }
    }

    internal override void Save(DataStream writer, List<int> relocTable, out long startOffset)
    {
        long offset = writer.Position;
        foreach (ShaderBlockResource resource in Resources)
        {
            writer.WriteInt32(resource.Index);
        }
        Pad(writer, 8);

        base.Save(writer, relocTable, out startOffset);

        writer.WriteInt64(offset);
        writer.WriteInt64(Resources.Count);
    }

    internal static void Pad(DataStream writer, int alignment)
    {
        while (writer.Position % alignment != 0)
        {
            writer.WriteByte(0);
        }
    }
}

internal class ShaderBlockEntry : ShaderStaticParamDbBlock
{
}

internal class ShaderPersistentParamDbBlock : ShaderBlockResource
{
    public List<ParameterEntry> Parameters = new();

    public override void Read(DataStream reader, List<ShaderBlockResource>? shaderBlockEntries)
    {
        base.Read(reader, shaderBlockEntries);

        long offset = reader.ReadInt64();
        reader.ReadInt64(); // size, recomputed on save

        reader.Position = offset;
        int count = reader.ReadInt32();
        for (int i = 0; i < count; i++)
        {
            Parameters.Add(new ParameterEntry(reader));
        }
    }

    internal override void Save(DataStream writer, List<int> relocTable, out long startOffset)
    {
        long offset = writer.Position;
        writer.WriteInt32(Parameters.Count);
        foreach (ParameterEntry param in Parameters)
        {
            param.Write(writer);
        }
        long size = writer.Position - offset;
        ShaderStaticParamDbBlock.Pad(writer, 8);

        base.Save(writer, relocTable, out startOffset);

        writer.WriteInt64(offset);
        writer.WriteInt64(size);
    }
}

internal class MeshParamDbBlock : ShaderBlockResource
{
    public Guid MeshAssetGuid;
    public int LodIndex;
    public List<ParameterEntry> Parameters = new();

    public override void Read(DataStream reader, List<ShaderBlockResource>? shaderBlockEntries)
    {
        base.Read(reader, shaderBlockEntries);

        long offset = reader.ReadInt64();
        reader.ReadInt32(); // size, recomputed on save
        LodIndex = reader.ReadInt32();
        MeshAssetGuid = reader.ReadGuid();

        reader.Position = offset;
        int count = reader.ReadInt32();
        for (int i = 0; i < count; i++)
        {
            Parameters.Add(new ParameterEntry(reader));
        }
    }

    internal override void Save(DataStream writer, List<int> relocTable, out long startOffset)
    {
        long offset = writer.Position;
        writer.WriteInt32(Parameters.Count);
        foreach (ParameterEntry param in Parameters)
        {
            param.Write(writer);
        }
        int size = (int)(writer.Position - offset);
        ShaderStaticParamDbBlock.Pad(writer, 8);

        base.Save(writer, relocTable, out startOffset);

        writer.WriteInt64(offset);
        writer.WriteInt32(size);
        writer.WriteInt32(LodIndex);
        writer.WriteGuid(MeshAssetGuid);
    }
}

internal class ShaderBlockMeshVariationEntry : ShaderBlockResource
{
    public List<Guid> RvmShaderRefGuids = new();
    public List<int> RvmShaderRefInts = new();

    public override void Read(DataStream reader, List<ShaderBlockResource>? shaderBlockEntries)
    {
        base.Read(reader, shaderBlockEntries);

        long offset = reader.ReadInt64();
        long count = reader.ReadInt64();

        reader.Position = offset;
        for (long i = 0; i < count; i++)
        {
            RvmShaderRefGuids.Add(reader.ReadGuid());
            RvmShaderRefInts.Add(reader.ReadInt32());
        }
    }

    internal override void Save(DataStream writer, List<int> relocTable, out long startOffset)
    {
        long offset = writer.Position;
        for (int i = 0; i < RvmShaderRefGuids.Count; i++)
        {
            writer.WriteGuid(RvmShaderRefGuids[i]);
            writer.WriteInt32(RvmShaderRefInts[i]);
        }
        ShaderStaticParamDbBlock.Pad(writer, 8);

        base.Save(writer, relocTable, out startOffset);

        writer.WriteInt64(offset);
        writer.WriteInt64(RvmShaderRefGuids.Count);
    }
}

/// <summary>The game's full depot, with the delta's blocks swapped in.</summary>
internal class ShaderBlockDepot
{
    private readonly List<ShaderBlockResource> m_resources = new();

    private static ShaderBlockResource CreateByType(long type) => type switch
    {
        0 => new ShaderBlockEntry(),
        1 => new ShaderPersistentParamDbBlock(),
        2 => new ShaderStaticParamDbBlock(),
        3 => new MeshParamDbBlock(),
        4 => new ShaderBlockMeshVariationEntry(),
        _ => throw new Exception($"Unknown shader block type {type}"),
    };

    private static long TypeOf(ShaderBlockResource resource) => resource switch
    {
        // Order matters: ShaderBlockEntry extends ShaderStaticParamDbBlock.
        ShaderBlockEntry => 0,
        MeshParamDbBlock => 3,
        ShaderPersistentParamDbBlock => 1,
        ShaderBlockMeshVariationEntry => 4,
        ShaderStaticParamDbBlock => 2,
        _ => throw new Exception("Unknown shader block class"),
    };

    public ShaderBlockDepot(Block<byte> inPayload, byte[] inMeta, ModifiedShaderBlockDepot inDelta)
    {
        using BlockStream reader = new(inPayload, false);

        int count = BitConverter.ToInt32(inMeta, 0x0c);
        List<long> offsets = new(count);
        for (int i = 0; i < count; i++)
        {
            offsets.Add(reader.ReadInt64());
            m_resources.Add(CreateByType(reader.ReadInt64()));
        }

        // v1's exact mechanics, object identity included: shells are read in
        // place (parents hold references to them), then the top-level slot is
        // swapped for the delta's block where the hash matches.
        for (int i = 0; i < count; i++)
        {
            reader.Position = offsets[i];
            m_resources[i].Read(reader, m_resources);

            ShaderBlockResource? replacement = inDelta.Find(m_resources[i].Hash);
            if (replacement is not null)
            {
                m_resources[i] = replacement;
            }
            m_resources[i].Index = i;
        }
    }

    public Block<byte> ToBytes(byte[] meta)
    {
        Block<byte> block = new(0);
        BlockStream writer = new(block, true);

        for (int i = 0; i < m_resources.Count; i++)
        {
            writer.WriteInt64(0);
            writer.WriteInt64(0);
        }

        List<long> offsets = new(m_resources.Count);
        List<int> relocTable = new();
        for (int i = 0; i < m_resources.Count; i++)
        {
            m_resources[i].Save(writer, relocTable, out long offset);
            offsets.Add(offset);
            relocTable.Add(i * 0x10);
        }
        while (writer.Position % 0x10 != 0)
        {
            writer.WriteByte(0);
        }

        long dataLength = writer.Length;

        writer.Position = 0;
        for (int i = 0; i < offsets.Count; i++)
        {
            writer.WriteInt64(offsets[i]);
            writer.WriteInt64(TypeOf(m_resources[i]));
        }

        // The res meta carries the payload's dimensions; same magics and
        // fields v1 checks and rewrites.
        if (BitConverter.ToUInt16(meta, 0) != 0x000A || BitConverter.ToUInt16(meta, 2) != 0x5B06)
        {
            throw new Exception("Unexpected ShaderBlockDepot res meta");
        }
        BitConverter.GetBytes((uint)dataLength).CopyTo(meta, 4);
        BitConverter.GetBytes((uint)(relocTable.Count * 4)).CopyTo(meta, 8);
        BitConverter.GetBytes((uint)m_resources.Count).CopyTo(meta, 12);

        writer.Position = dataLength;
        foreach (int reloc in relocTable)
        {
            writer.WriteInt32(reloc);
        }

        // The growable block keeps its CAPACITY as its size; the game's
        // originalSize must be the written length (v1: data + reloc table),
        // or the payload carries trailing garbage the game then reads.
        block.Resize((int)writer.Position);
        return block;
    }
}

/// <summary>The delta a mod ships: changed blocks keyed by hash.</summary>
internal class ModifiedShaderBlockDepot
{
    private readonly List<ulong> m_hashes = new();
    private readonly List<ShaderBlockResource> m_resources = new();

    public ModifiedShaderBlockDepot(Block<byte> inPayload)
    {
        using BlockStream reader = new(inPayload, false);

        int count = reader.ReadInt32();
        while (reader.Position % 0x10 != 0)
        {
            reader.Position += 1;
        }

        List<long> offsets = new(count);
        for (int i = 0; i < count; i++)
        {
            offsets.Add(reader.ReadInt64());
            m_resources.Add(CreateByType(reader.ReadInt64()));
        }

        for (int i = 0; i < count; i++)
        {
            reader.Position = offsets[i];
            // Within-delta references only. v1 passed null here, so any mod
            // that worked under v1 never consults the list at all.
            m_resources[i].Read(reader, m_resources);
            m_hashes.Add(m_resources[i].Hash);
        }
    }

    private static ShaderBlockResource CreateByType(long type) => type switch
    {
        0 => new ShaderBlockEntry(),
        1 => new ShaderPersistentParamDbBlock(),
        2 => new ShaderStaticParamDbBlock(),
        3 => new MeshParamDbBlock(),
        4 => new ShaderBlockMeshVariationEntry(),
        _ => throw new Exception($"Unknown shader block type {type}"),
    };

    public ShaderBlockResource? Find(ulong hash)
    {
        int index = m_hashes.IndexOf(hash);
        return index == -1 ? null : m_resources[index];
    }

    public void Merge(ModifiedShaderBlockDepot newer)
    {
        for (int i = 0; i < m_hashes.Count; i++)
        {
            ShaderBlockResource? replacement = newer.Find(m_hashes[i]);
            if (replacement is not null)
            {
                m_resources[i] = replacement;
            }
        }
        for (int i = 0; i < newer.m_hashes.Count; i++)
        {
            if (!m_hashes.Contains(newer.m_hashes[i]))
            {
                m_hashes.Add(newer.m_hashes[i]);
                m_resources.Add(newer.m_resources[i]);
            }
        }
    }
}
