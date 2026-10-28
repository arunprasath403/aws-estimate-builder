import forms


def fargate(values, **extra):
    return forms.normalize({"id": "i1", "service": "awsFargate", "region": "ap-south-1", "values": values, **extra})


def test_unknown_service_is_unsupported():
    it = forms.normalize({"id": "i1", "service": "notAService", "values": {}})
    assert it["status"] == "unsupported"


def test_missing_region_is_a_blocking_question():
    it = forms.normalize({"id": "i1", "service": "amazonApiGateway", "values": {}})
    assert it["questions"][0] == "region"
    assert "region" in it["blocking"]
    assert it["status"] == "needs_input"


def test_always_on_count_is_moved_to_per_month():
    it = fargate({"numberOfTasks": {"value": 4, "unit": "perHour"}, "taskDuration": {"value": 730, "unit": "hr"}})
    assert it["values"]["numberOfTasks"] == {"value": "4", "unit": "perMonth"}
    assert any("per month" in w for w in it["warnings"])


def test_short_batch_tasks_keep_their_rate():
    it = fargate({"numberOfTasks": {"value": 4, "unit": "perHour"}, "taskDuration": {"value": 10, "unit": "min"}})
    assert it["values"]["numberOfTasks"]["unit"] == "perHour"
    assert not it["warnings"]


def test_raw_count_in_a_million_field_is_converted():
    it = forms.normalize({"id": "i1", "service": "amazonSimpleQueueService", "region": "ap-south-1",
                          "values": {"standardQueueRequests": {"value": 30_000_000, "unit": "perMonth"}}})
    assert it["values"]["standardQueueRequests"]["value"] == "30"
    assert any("million" in w for w in it["warnings"])


def test_small_value_in_a_million_field_is_left_alone():
    it = forms.normalize({"id": "i1", "service": "amazonSimpleQueueService", "region": "ap-south-1",
                          "values": {"standardQueueRequests": {"value": 30, "unit": "perMonth"}}})
    assert it["values"]["standardQueueRequests"]["value"] == "30"


def test_size_unit_matches_on_size_part():
    it = forms.normalize({"id": "i1", "service": "dynamoDbOnDemand", "region": "ap-south-1",
                          "values": {"dataStorageSize": {"value": 2, "unit": "TB"}}})
    assert it["values"]["dataStorageSize"] == {"value": "2", "unit": "tb|NA"}


def test_dropdown_accepts_label_and_rejects_unknown():
    ok = fargate({"operatingSystem": "Linux"})
    assert ok["values"]["operatingSystem"] == {"value": "linux"}
    bad = fargate({"operatingSystem": "Solaris"})
    assert "operatingSystem" not in bad["values"]
    assert bad["errors"]


def test_instance_rows_drop_guessable_columns_and_fill_defaults():
    it = forms.normalize({"id": "i1", "service": "amazonElastiCache", "region": "ap-south-1", "values": {
        "instances": [{"Number of Nodes": 2, "Instance Type": "cache.t4g.small", "Instance Family": "t4g"}]}})
    row = it["values"]["columnFormIPM"]["value"][0]
    assert "Instance Family" not in row  # a wrong guess here prices the row at $0
    assert row["TermType"] == {"value": "OnDemand"}
    assert row["undefined"]["value"]["unit"] == "100"


def test_yes_no_addons_default_to_no():
    it = forms.normalize({"id": "i1", "service": "amazonRDSPostgreSQLDB", "region": "ap-south-1", "values": {}})
    assert it["values"]["createRDSProxy"] == {"value": "0"}
    assert it["values"]["DatabaseInsightsSelected"] == {"value": "0"}


def test_skipped_questions_are_not_asked_again():
    it = forms.normalize({"id": "i1", "service": "amazonRDSPostgreSQLDB", "region": "ap-south-1",
                          "values": {}, "questions": ["additionalBackupStorage"],
                          "skipped": ["additionalBackupStorage"]})
    assert "additionalBackupStorage" not in it["questions"]


def test_ec2_requires_core_fields():
    it = forms.normalize({"id": "i1", "service": "ec2Enhancement", "region": "ap-south-1", "values": {"quantity": 2}})
    assert {"instanceType", "os", "hours"} <= set(it["blocking"])
