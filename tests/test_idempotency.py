from app.idempotency import DuplicateStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_records_and_detects_within_window():
    clock = FakeClock()
    store = DuplicateStore(window_seconds=60, clock=clock)
    assert not store.seen_recently("a")
    store.record("a")
    assert store.seen_recently("a")
    clock.now += 59
    assert store.seen_recently("a")


def test_entries_expire_after_window():
    clock = FakeClock()
    store = DuplicateStore(window_seconds=60, clock=clock)
    store.record("a")
    clock.now += 61
    assert not store.seen_recently("a")
    assert len(store) == 0


def test_store_is_bounded():
    store = DuplicateStore(window_seconds=60, max_entries=3, clock=FakeClock())
    for key in "abcde":
        store.record(key)
    assert len(store) == 3
    assert not store.seen_recently("a")
    assert store.seen_recently("e")


def test_zero_window_disables_detection():
    store = DuplicateStore(window_seconds=0)
    store.record("a")
    assert not store.seen_recently("a")
    assert len(store) == 0
