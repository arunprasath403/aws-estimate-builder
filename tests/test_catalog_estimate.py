import catalog
import estimate
import forms


def test_catalog_covers_hundreds_of_services():
    assert len(catalog.selectable()) >= 400


def test_resolve_tolerates_model_variations():
    assert catalog.resolve("amazonDynamoDbOnDemand") == "dynamoDbOnDemand"
    assert catalog.resolve("Amazon RDS for PostgreSQL") == "amazonRDSPostgreSQLDB"
    assert catalog.resolve("definitelyNotAService") is None


def test_cond_ok():
    c = {"or": [{"op": "==", "id": "vcpu", "value": 1}, {"op": "==", "id": "vcpu", "value": 2}]}
    assert catalog.cond_ok(c, {"vcpu": {"value": "1"}})
    assert not catalog.cond_ok(c, {"vcpu": {"value": "4"}})
    assert catalog.cond_ok(c, {})  # unknown inputs count as satisfied


def test_build_wraps_child_services_in_their_group():
    it = forms.normalize({"id": "s3", "service": "amazonS3Standard", "region": "ap-south-1",
                          "values": {"s3StandardStorageSize": {"value": 500, "unit": "gb|month"}}})
    est, keys = estimate.build([it], "test")
    entry = est["services"][keys["s3"]]
    assert entry["serviceCode"] == "amazonSimpleStorageServiceGroup"
    assert entry["subServices"][0]["serviceCode"] == "amazonS3Standard"
    assert entry["subServices"][0]["calculationComponents"]["s3StandardStorageSize"]["value"] == "500"


def test_build_includes_defaults_and_drops_hidden_fields():
    it = forms.normalize({"id": "f", "service": "awsFargate", "region": "ap-south-1", "values": {
        "vcpuPerTask": "1", "smallMemory": "2", "memoryStandardFargateOnDemand": {"value": 2, "unit": "gb"}}})
    it["status"] = "ready"
    est, keys = estimate.build([it], "test")
    comps = est["services"][keys["f"]]["calculationComponents"]
    assert "smallMemory" not in comps          # only shown for 0.25 vCPU
    assert comps["memoryStandardFargateOnDemand"]["value"] == "2"
    assert comps["operatingSystem"]["value"]   # default filled in, as the calculator's form would


def test_ec2_builder():
    it = forms.normalize({"id": "e", "service": "ec2Enhancement", "region": "ap-south-1", "values": {
        "instanceType": "T4G.Nano", "quantity": 2, "os": "linux", "hours": 365, "storageGb": 30}})
    assert it["status"] == "ready"
    est, keys = estimate.build([it], "test")
    cc = est["services"][keys["e"]]["calculationComponents"]
    assert cc["instanceType"]["value"] == "t4g.nano"
    assert cc["workload"]["value"]["data"] == "2"
    assert cc["pricingStrategy"]["value"]["utilizationValue"] == "50"


def test_resolve_abbreviations_and_backup_parts():
    assert catalog.resolve("Amazon MSK") == "amazonManagedStreamingForApacheKafkaMsk"
    assert catalog.resolve("Amazon EBS") == "amazonElasticBlockStore"  # not AWS Backup's "EBS" part
    assert catalog.resolve("EBS Backup") == "amazonEbsBackup"
    assert catalog.resolve("Amazon S3") is None  # ambiguous: the user picks the storage class
