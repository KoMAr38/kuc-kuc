"""What only the Firestore store does. Skipped unless KUCKUC_TEST_FIRESTORE_PROJECT is set."""

from datetime import UTC, datetime, timedelta

from factories import T0, make_call, make_episode, make_person


def _doc(store, collection, id):
    return store._col(collection).document(id).get().to_dict()


def test_demo_data_carries_an_expiry_a_day_ahead_and_real_data_none(firestore_store):
    s = firestore_store
    s.put_person(make_person(id="demo-abc", demo=True))
    s.put_person(make_person(id="p1"))
    s.create_episode(make_episode(id="demo-ep1", person_id="demo-abc"))
    s.put_call(make_call(id="c1", episode_id="demo-ep1"))
    s.add_timeline("demo-ep1", "call_placed", {"role": "person"})

    ahead = _doc(s, "persons", "demo-abc")["expires_at"] - datetime.now(UTC)
    assert timedelta(hours=23) < ahead < timedelta(hours=25)
    assert "expires_at" not in _doc(s, "persons", "p1")
    assert "expires_at" in _doc(s, "episodes", "demo-ep1")
    assert "expires_at" in _doc(s, "calls", "c1")
    (row,) = s._col("episodes").document("demo-ep1").collection("timeline").stream()
    assert "expires_at" in row.to_dict()


def test_episode_keeps_its_expiry_after_an_update(firestore_store):
    s = firestore_store
    s.create_episode(make_episode(id="demo-ep2", person_id="demo-abc"))
    s.update_episode("demo-ep2", lambda e: e.model_copy(update={"person_attempts": 1}))
    doc = _doc(s, "episodes", "demo-ep2")
    assert doc["person_attempts"] == 1 and "expires_at" in doc


def test_placed_calls_feed_the_public_counters(firestore_store):
    s = firestore_store
    s.create_episode(make_episode(id="e1"))
    s.put_call(make_call(id="c1", created_at=T0))
    s.put_call(make_call(id="c2", created_at=T0))
    before = s.stats()
    s.patch_call("c1", status="placed", provider_sid="CA1")
    s.patch_call("c2", status="blocked")
    after = s.stats()
    assert after["calls_total"] == before["calls_total"] + 1
    assert after["calls_today"] == before["calls_today"] + 1
    assert s.get_call("c1").provider_sid == "CA1"


def test_timeline_rows_get_one_clock_and_stable_numbers(firestore_store):
    s = firestore_store
    for t in ("a", "b", "c"):
        s.add_timeline("e9", t, {"k": t})
    rows = s.list_timeline("e9")
    assert [(r.seq, r.type, r.public) for r in rows] == [(1, "a", {"k": "a"}), (2, "b", {"k": "b"}), (3, "c", {"k": "c"})]
    assert rows[0].at <= rows[1].at <= rows[2].at


def test_simulated_data_from_the_load_test_expires_like_demo_data(firestore_store):
    s = firestore_store
    s.put_person(make_person(id="sim-abc", sim=True))
    s.create_episode(make_episode(id="sim-abc", person_id="sim-abc"))
    s.update_episode("sim-abc", lambda e: e.model_copy(update={"person_attempts": 1}))
    s.put_call(make_call(id="c9", episode_id="sim-abc"))
    s.add_timeline("sim-abc", "call_queued", {"role": "person"})

    ahead = _doc(s, "persons", "sim-abc")["expires_at"] - datetime.now(UTC)
    assert timedelta(hours=23) < ahead < timedelta(hours=25)
    assert "expires_at" in _doc(s, "episodes", "sim-abc")
    assert "expires_at" in _doc(s, "calls", "c9")
    (row,) = s._col("episodes").document("sim-abc").collection("timeline").stream()
    assert "expires_at" in row.to_dict()
    s.put_person(make_person(id="simona"))  # a real id that merely begins with the same letters
    assert "expires_at" not in _doc(s, "persons", "simona")
