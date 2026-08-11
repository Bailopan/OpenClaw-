import json

from supplier_radar.checkpoint import CheckpointWriter


def test_checkpoint_writer_appends_jsonl_and_flushes(tmp_path):
    path = tmp_path / "run.jsonl"
    writer = CheckpointWriter(path)

    writer.append("batch_start", batch=1, queries=100)
    writer.append("batch_finish", batch=1, requests_completed=100, estimated_cost_rub=3.05)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"event": "batch_start", "batch": 1, "queries": 100},
        {
            "event": "batch_finish",
            "batch": 1,
            "requests_completed": 100,
            "estimated_cost_rub": 3.05,
        },
    ]
