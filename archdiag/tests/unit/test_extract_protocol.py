from archdiag.facts.extract_protocol import extract_dbc, extract_eds
from archdiag.facts.model import MESSAGE, PROTOCOL_FILE, Confidence

DBC = """VERSION ""
BU_: ECU1 ECU2 GATEWAY
BO_ 256 EngineData: 8 ECU1
 SG_ Speed : 0|16@1+ (0.1,0) [0|6553.5] "km/h" ECU2,GATEWAY
 SG_ Rpm : 16|16@1+ (1,0) [0|8000] "rpm" ECU2
BO_ 2147484160 ExtendedStatus: 8 Vector__XXX
 SG_ Flag : 0|1@1+ (1,0) [0|1] "" Vector__XXX
"""

EDS = """[FileInfo]
FileName=node.eds
[1000]
ParameterName=Device type
DataType=0x0007
[1018]
ParameterName=Identity object
SubNumber=2
[1018sub1]
ParameterName=Vendor-ID
[6081]
ParameterName=Profile velocity
"""


def test_dbc_messages_with_senders_and_receivers() -> None:
    facts = extract_dbc("net/vehicle.dbc", DBC)
    assert all(f.kind == MESSAGE and f.source == PROTOCOL_FILE for f in facts)
    assert all(f.confidence == Confidence.HIGH for f in facts)
    assert [f.data for f in facts] == [
        {
            "protocol": "can",
            "id": 256,
            "name": "EngineData",
            "senders": ["ECU1"],
            "receivers": ["ECU2", "GATEWAY"],
        },
        {
            "protocol": "can",
            "id": 0x200,
            "name": "ExtendedStatus",
            "senders": [],
            "receivers": [],
            "extended": True,
        },
    ]
    assert [f.line for f in facts] == [3, 6]


def test_eds_objects() -> None:
    facts = extract_eds("od/node.eds", EDS)
    assert [(f.data["id"], f.data.get("subindex"), f.data["name"]) for f in facts] == [
        (0x1000, None, "Device type"),
        (0x1018, None, "Identity object"),
        (0x1018, 1, "Vendor-ID"),
        (0x6081, None, "Profile velocity"),
    ]
    assert all(f.data["protocol"] == "canopen" for f in facts)


def test_malformed_input_yields_nothing() -> None:
    assert extract_dbc("x.dbc", "BO_ nope\n") == []
    assert extract_eds("x.eds", "[zzzz]\nParameterName=x\n") == []
