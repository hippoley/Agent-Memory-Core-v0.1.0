"""Small deterministic example handler for the Temporal durable-task profile.

This example only demonstrates resumable checkpointing. Replace it with
application-owned handlers before using the runtime for real business work.
"""


def organize_memory_batch(task, context):
    items = task["input"].get("items", [])
    checkpoint = context.checkpoint or {}
    cursor = int(checkpoint.get("cursor", 0))
    for index in range(cursor, len(items)):
        item = items[index]
        context.commit_checkpoint(
            checkpoint={"cursor": index + 1, "last_item_id": item.get("id")},
            idempotency_key=f"{task['task_id']}:checkpoint:{index + 1}",
            progress_completed=index + 1,
            progress_total=len(items),
        )
    return {"processed": len(items), "resumed_from": cursor}


HANDLERS = {
    "example.memory.batch.organize": organize_memory_batch,
}
