import io
import os
import time
from typing import Dict, Optional, Union

from UE4Parse import Logger
from UE4Parse.BinaryReader import BinaryStream
from UE4Parse.Exceptions.Exceptions import InvalidEncryptionKey
from UE4Parse.IO.IoObjects.FIoStoreEntry import FIoStoreEntry
from UE4Parse.PakFile.PakObjects.EPakVersion import EPakVersion
from UE4Parse.PakFile.PakObjects.FPakCompressedBlock import FPakCompressedBlock
from UE4Parse.PakFile.PakObjects.FPakDirectoryEntry import FPakDirectoryEntry
from UE4Parse.PakFile.PakObjects.FPakEntry import FPakEntry
from UE4Parse.PakFile.PakObjects.FPakInfo import PakInfo
from UE4Parse.PakFile.PakObjects.FSHAHash import FSHAHash

CrytoAval = True
try:
    from Crypto.Cipher import AES
except ImportError:
    CrytoAval = False

logger = Logger.get_logger(__name__)


class PakReader:
    # @profile
    def __init__(self, File: str = "", Case_insensitive: bool = False, reader: Optional[BinaryStream] = None) -> None:
        self.MountPoint: str = ""
        if reader is not None:
            self.reader = reader
            self.size: int = reader.size
        else:
            self.reader: BinaryStream = BinaryStream(File)
            self.size: int = os.path.getsize(File)
        self.FileName: str = os.path.basename(File)
        self.Info = PakInfo(self.reader, self.size)
        self.NumEntries = -1
        self.reader.seek(self.Info.IndexOffset, 0)
        self.MountArray = self.reader.readBytes(128)
        self.Case_insensitive: bool = Case_insensitive

    def get_encryption_key_guid(self):
        return self.Info.EncryptionKeyGuid

    # @profile
    def ReadIndex(self, key: str = None):
        self.AesKey = key
        starttime = time.time()
        self.reader.seek(self.Info.IndexOffset, 0)

        if not self.Info.bEncryptedIndex:
            IndexReader = self.reader
        else:
            if not CrytoAval:
                raise ImportError(
                    "Failed to Import \"pycryptodome\", Index is Encrypted it is required for decryption.")
            if key is None:
                raise InvalidEncryptionKey("Index is Encrypted and Key was not provided.")

            bytekey = bytearray.fromhex(key)
            decryptor = AES.new(bytekey, AES.MODE_ECB)
            IndexReader = BinaryStream(io.BytesIO(decryptor.decrypt(self.reader.readBytes(self.Info.IndexSize))))

            stringLen = IndexReader.readInt32()
            if stringLen > 512 or stringLen < -512:
                raise InvalidEncryptionKey(f"Provided key didn't work with {self.FileName}")
            if stringLen < 0:
                IndexReader.base_stream.seek((stringLen - 1) * 2, 1)
                if IndexReader.readUInt16() != 0:
                    raise InvalidEncryptionKey(f"Provided key didn't work with {self.FileName}")
            else:
                IndexReader.base_stream.seek(stringLen - 1, 1)
                if int.from_bytes(IndexReader.readByte(), "little") != 0:
                    raise InvalidEncryptionKey(f"Provided key didn't work with {self.FileName}")
            IndexReader.seek(0, 0)

        if self.Info.Version.value >= EPakVersion.PATH_HASH_INDEX.value:
            index = self.ReadUpdatedIndex(IndexReader, key, self.Case_insensitive)
        else:
            self.MountPoint = IndexReader.readFString() or ""

            if self.MountPoint.startswith("../../.."):
                self.MountPoint = self.MountPoint[8::]

            # if self.Case_insensitive:
            #     self.MountPoint = self.MountPoint.lower()

            self.NumEntries = IndexReader.readInt32()

            tempfiles: Dict[str, FPakEntry] = {}
            for _ in range(self.NumEntries):
                entry = FPakEntry(IndexReader, self.Info.Version, self.Info.SubVersion, self.FileName)
                if self.Case_insensitive:
                    tempfiles[self.MountPoint.lower() + entry.Name.lower()] = entry
                else:
                    tempfiles[self.MountPoint + entry.Name] = entry

            index = UpdateIndex(self.FileName, self, tempfiles)
            del tempfiles

        time_taken = round(time.time() - starttime, 2)
        logger.info(
            f"{self.FileName} contains {self.NumEntries} files, mount point: {self.MountPoint}, version: {self.Info.Version.value}, in: {time_taken}s")

        return index

    def ReadUpdatedIndex(self, IndexReader: BinaryStream, key, Case_insensitive: bool) -> dict:
        self.MountPoint = IndexReader.readFString() or ""

        if self.MountPoint.startswith("../../.."):
            self.MountPoint = self.MountPoint[8::]

        if Case_insensitive:
            self.MountPoint = self.MountPoint.lower()

        self.NumEntries = IndexReader.readInt32()
        PathHashSeed = IndexReader.readUInt64()

        if IndexReader.readInt32() == 0:
            raise Exception("No path hash index")

        IndexReader.seek(8 + 8 + 20)  # PathHashIndexOffset(long) + PathHashIndexSize(long) + PathHashIndexHash(20bytes)

        if IndexReader.readInt32() == 0:
            raise Exception("No directory index")

        FullDirectoryIndexOffset = IndexReader.readInt64()
        FullDirectoryIndexSize = IndexReader.readInt64()
        FullDirectoryIndexHash = FSHAHash(IndexReader)

        PakEntry_Size = IndexReader.readInt32()
        EncodedPakEntries = IndexReader.readBytes(PakEntry_Size)  # TArray

        file_num = IndexReader.readInt32()
        if file_num < 0:
            raise Exception("Corrupt PrimaryIndex")

        self.reader.seek(FullDirectoryIndexOffset, 0)

        PathHashIndexData: bytes = self.reader.base_stream.read(FullDirectoryIndexSize)
        if self.Info.bEncryptedIndex:
            bytekey = bytearray.fromhex(key)
            decryptor = AES.new(bytekey, AES.MODE_ECB)
            PathHash_Reader = BinaryStream(io.BytesIO(decryptor.decrypt(PathHashIndexData)))
        else:
            PathHash_Reader = BinaryStream(io.BytesIO(PathHashIndexData))

        PathHashIndex = PathHash_Reader.readTArray_W_Arg(FPakDirectoryEntry, PathHash_Reader)
        PathHash_Reader.base_stream.close()

        encodedEntryReader = BinaryStream(io.BytesIO(EncodedPakEntries))
        tempentries = {}
        for directoryEntry in PathHashIndex:
            for hasIndexEntry in directoryEntry.Entries:
                path = directoryEntry.Directory + hasIndexEntry.FileName
                if Case_insensitive:
                    path = path.lower()

                encodedEntryReader.seek(hasIndexEntry.Location, 0)
                entry = self.BitEntry(path, encodedEntryReader)
                tempentries[self.MountPoint + path] = entry

        index = UpdateIndex(self.FileName, self, tempentries)

        del tempentries
        encodedEntryReader.base_stream.close()
        return index

    def BitEntry(self, name: str, reader: BinaryStream):
        # Grab the big bitfield value:
        # Bit 31 = Offset 32-bit safe?
        # Bit 30 = Uncompressed size 32-bit safe?
        # Bit 29 = Size 32-bit safe?
        # Bits 28-23 = Compression method
        # Bit 22 = Encrypted
        # Bits 21-6 = Compression blocks count
        # Bits 5-0 = Compression block size

        value = reader.readUInt32()

        # Filter out the CompressionMethod.
        compressionMethodIndex = ((value >> 23) & 0x3f)

        isOffset32BitSafe = (value & (1 << 31)) != 0
        offset = reader.readUInt32() if isOffset32BitSafe else reader.readInt64()

        isUncompressedSize32BitSafe = (value & (1 << 30)) != 0
        uncompressedSize = reader.readUInt32() if isUncompressedSize32BitSafe else reader.readInt64()

        if compressionMethodIndex != 0:
            # Size is only present if compression is applied.
            isSize32BitSafe = (value & (1 << 29)) != 0
            size = reader.readUInt32() if isSize32BitSafe else reader.readInt64()
        else:
            size = uncompressedSize

        encrypted = (value & (1 << 22)) != 0

        CompressionBlocksCount = (value >> 6) & 0xffff

        # Filter the compression block size or use the UncompressedSize if less that 64k.
        compressionBlockSize = 0
        if CompressionBlocksCount > 0:
            compressionBlockSize = uncompressedSize if uncompressedSize < 65536 else ((value & 0x3f) << 11)

        # Set bDeleteRecord to false, because it obviously isn't deleted if we are here.
        Deleted = False

        baseOffset = 0 if self.Info.Version.value >= EPakVersion.RELATIVE_CHUNK_OFFSETS.value else offset

        CompressionBlocks: list = []
        if CompressionBlocksCount == 0 and bool(encrypted):
            # If the number of CompressionBlocks is 1, we didn't store any extra information.
            # Derive what we can from the entry's file offset and size.
            start = baseOffset + FPakEntry.GetSize(EPakVersion.LATEST, compressionMethodIndex, CompressionBlocksCount)
            CompressionBlocks.append(FPakCompressedBlock(None, start, start + size))
        elif len(CompressionBlocks) > 0:
            CompressedBlockAlignment = 16 if encrypted else 1
            CompressedBlockOffset = baseOffset + FPakEntry.GetSize(EPakVersion.LATEST, compressionMethodIndex,
                                                                   CompressionBlocksCount)

            for _ in CompressionBlocks:
                compressedStart = CompressedBlockOffset
                compressedEnd = CompressedBlockOffset + reader.readUInt32()
                CompressionBlocks.append(FPakCompressedBlock(None, compressedStart, compressedEnd))

                align = compressedEnd - compressedStart
                CompressedBlockOffset += align + CompressedBlockAlignment - (align % CompressedBlockAlignment)

        entry = FPakEntry(None)
        entry.Name = name
        entry.ContainerName = self.FileName
        entry.CompressionBlocks = CompressionBlocks
        entry.Size = size
        entry.Encrypted = encrypted
        entry.UncompressedSize = uncompressedSize
        entry.Offset = offset
        entry.CompressionMethodIndex = compressionMethodIndex
        entry.Deleted = Deleted
        entry.StructSize = FPakEntry.GetSize(EPakVersion.LATEST, compressionMethodIndex,
                                             len(CompressionBlocks))
        return entry


# @profile
def UpdateIndex(FileName, Container, Index: Dict[str, Union[FPakEntry, FIoStoreEntry]]) -> Dict[
    str, Union[FPakEntry, FIoStoreEntry]]:
    def removeslash(string):
        if string.startswith("/"):
            return string[1:]
        return string

    index: Dict[str, Union[FPakEntry, FIoStoreEntry]] = {}
    for entry, IndexEntry in Index.items():
        if entry.endswith(".uexp") or entry.endswith(".ubulk"):
            continue

        PathNoext = os.path.splitext(entry)[0]
        uexp = PathNoext + ".uexp"
        ubulk = PathNoext + ".ubulk"
        uptnl = PathNoext + ".uptnl"

        if uexp in Index:
            IndexEntry.uexp = Index[uexp]
            IndexEntry.hasUexp = True
        else:
            IndexEntry.hasUexp = False

        if ubulk in Index:
            IndexEntry.ubulk = Index[ubulk]
            IndexEntry.hasUbulk = True
        else:
            IndexEntry.hasUbulk = False

        if uptnl in Index:
            IndexEntry.uptnl = Index[uptnl]
            IndexEntry.hasUptnl = True
        else:
            IndexEntry.hasUptnl = False

        index[removeslash(PathNoext)] = IndexEntry

    return index
