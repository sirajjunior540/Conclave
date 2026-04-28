"""Notification adapter — v0 console implementation.

Replace with a real Slack adapter in slice 1: same function signatures, but
backed by Slack Web API (chat.postMessage for channels, conversations.open
+ chat.postMessage for DMs).
"""
import sys


def send_dm(slack_user_id: str, message: str) -> None:
    print(f"\n[DM -> {slack_user_id}]\n{message}\n", file=sys.stderr, flush=True)


def post_to_channel(channel_id: str, message: str) -> None:
    print(f"\n[#{channel_id}] {message}\n", file=sys.stderr, flush=True)
