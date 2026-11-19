"""Example application handlers for the standalone Durable Task Worker.

This sample demonstrates checkpoint/restart mechanics only. Replace the
placeholder item processing with your own idempotent domain operation.
"""


def organize_memory_batch(task, context):
    items=task["input"].get("items",[])
    checkpoint=context.checkpoint or {}
    cursor=int(checkpoint.get("cursor",0))
    for index in range(cursor,len(items)):
        # Replace this line with a real, idempotent memory-domain operation.
        item=items[index]
        context.commit_checkpoint(
            checkpoint={"cursor":index+1,"last_item_id":item.get("id")},
            idempotency_key=f"{task['task_id']}:checkpoint:{index+1}",
            progress_completed=index+1,
            progress_total=len(items),
        )
    return {"processed":len(items),"resumed_from":cursor}


HANDLERS = {
    "example.memory.batch.organize": organize_memory_batch,
}
