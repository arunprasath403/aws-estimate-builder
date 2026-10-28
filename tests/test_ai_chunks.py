import ai


def test_short_document_is_one_part():
    text = "Service | Size\nEC2 | t4g.nano"
    assert ai._chunks(text) == [text]


def test_long_table_keeps_header_in_every_part():
    header = "Service | Size | Qty"
    rows = [f"EC2 | t4g.nano | {i}" for i in range(60)]
    parts = ai._chunks("\n".join([header] + rows))
    assert len(parts) == 3
    assert all(p.splitlines()[0] == header for p in parts)
    assert sum(len(p.splitlines()) - 1 for p in parts) == 60


def test_long_prose_is_split_without_header():
    lines = [f"We run {i} web servers on m7g.large." for i in range(40)]
    parts = ai._chunks("\n".join(lines))
    assert len(parts) == 2
    assert parts[1].splitlines()[0] == lines[25]


def test_free_resources_are_recognised_but_paid_tiers_are_not():
    def free(text):
        return bool(ai.FREE.search(text)) and not ai.PAID.search(text)
    assert free("DDoS protection | AWS Shield | Shield Standard")
    assert free("Certificates | AWS Certificate Manager | Public certificates")
    assert not free("AWS Shield Advanced subscription")
    assert not free("AWS Certificate Manager Private CA | 2 private certificate authorities")
    assert not free("Kafka cluster | Amazon MSK | kafka.m7g.large")
