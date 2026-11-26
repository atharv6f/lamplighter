"""Sends a session's waiting messages to its channel, oldest first."""

from lamplighter.ports import Channel, Failure, Outbox


async def deliver_due(
    session_id: str, outbox: Outbox, channel: Channel
) -> Failure | None:
    """Send each of the session's unsent messages, in the order they were enqueued.

    Each send is saved as begun before it is made, so a kill during it
    leads to a second copy that says it may be a repeat. A send that fails
    is saved as failed and ends this call, so a later message never
    overtakes an earlier one. It is tried again the next time. The send's
    `Failure` is returned, so that it is not hidden, and so is a `Failure`
    from the outbox.
    """
    due = await outbox.due()
    if isinstance(due, Failure):
        return due
    for out in due:
        if out.session_id != session_id:
            continue
        started = await outbox.start_send(out.out_id)
        if isinstance(started, Failure):
            return started
        sent = await channel.send(out)
        if isinstance(sent, Failure):
            failed = await outbox.mark_failed(out.out_id, sent)
            return failed if isinstance(failed, Failure) else sent
        marked = await outbox.mark_sent(out.out_id, sent)
        if isinstance(marked, Failure):
            return marked
    return None
