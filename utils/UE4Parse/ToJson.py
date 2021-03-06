from UE4Parse.IoObjects.FExportMapEntry import FExportMapEntry
from UE4Parse.IoObjects.IoUtils import resolveObjectIndex
from UE4Parse.IoObjects.FPackageObjectIndex import FPackageObjectIndex
from typing import List, TYPE_CHECKING

from UE4Parse.Objects.FNameEntrySerialized import FNameEntrySerialized
from UE4Parse.Objects.FObjectExport import FObjectExport
from UE4Parse.Objects.FObjectImport import FObjectImport

if TYPE_CHECKING:
    from .PackageReader import Package

def ToJson(PackageReader: 'Package'):
    NameMap: List[FNameEntrySerialized] = PackageReader.NameMap
    ImportMap: List[FObjectImport] = PackageReader.ImportMap
    ExportMap: List[FObjectExport, FExportMapEntry] = PackageReader.ExportMap
    Dict = {"Exports": []}
    
    # "ImportMap": [], "ExportMap": []
    # for Import in ImportMap:
    #     # if isinstance(Import, FPackageObjectIndex):
    #     #     Import = resolveObjectIndex(PackageReader, PackageReader.Provider.GlobalData, Import)
    #     Dict["ImportMap"].append(Import.GetValue())

    for Export in ExportMap:
        Dict["Exports"].append({"ExportType": Export.name.string, "ExportName": Export.type.string, "ExportValue": Export.exportObject.GetValue()})
        # Dict["ExportMap"].append(Export.GetValue())

    if NameMap is not None:
        Dict["NameMap"] = []
        for Name in NameMap:
            Dict["NameMap"].append(Name.GetValue())

    return Dict
