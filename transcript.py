"""Writes every conversation to the database, line by line.

One document per session in the `conversations` collection. Each line is
pushed from a background thread the moment it happens, so the audio loop
never waits on the network and a crash or Ctrl+C loses nothing already said.
"""
import queue
import threading
from datetime import datetime

import db


class Transcript:
    def __init__(self):
        self.col = db.connect().conversations
        started = datetime.now()
        self.id = self.col.insert_one({
            "started": started, "ended": None, "lines": [], "turns": [],
        }).inserted_id
        self.name = started.strftime("%Y-%m-%d %H:%M:%S")
        self.q = queue.Queue()
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

    def _drain(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            field, value = item
            try:
                self.col.update_one({"_id": self.id}, {"$push": {field: value}})
            except Exception as e:
                print(f"[log] write failed: {e}")

    def _line(self, who, text):
        self.q.put(("lines", {"at": datetime.now(), "who": who, "text": text}))

    def user(self, text):
        self._line("user", text)

    def priya(self, text):
        self._line("priya", text)

    def tool(self, name, args, result):
        self._line("tool", f"{name}({args}) -> {result}")

    def note(self, event):
        self._line("note", event)

    def timing(self, number, marks_ms):
        """Per-turn latency marks, in ms from the moment the user stopped."""
        self.q.put(("turns", {"turn": number, **marks_ms}))

    def close(self):
        self.q.put(None)
        self.worker.join(timeout=5)
        try:
            self.col.update_one({"_id": self.id},
                                {"$set": {"ended": datetime.now()}})
        except Exception as e:
            print(f"[log] close failed: {e}")
