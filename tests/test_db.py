"""Tests for the SQLite data layer."""
from __future__ import annotations

import pytest

from bot import db


@pytest.fixture()
def temp_db(tmp_path):
    db.init_db(tmp_path / "test.db")
    yield
    db._db_path = None


def test_register_and_get_user(temp_db):
    db.register_user(
        user_id=1,
        username="alice",
        full_name="Alice Doe",
        city="Berlin",
        occupation="Student",
        photo_file_id="photo_1",
        bio="Hi, I'm Alice.",
    )
    user = db.get_user(1)
    assert user is not None
    assert user["full_name"] == "Alice Doe"
    assert user["status"] == "active"


def test_reregister_updates_existing(temp_db):
    db.register_user(user_id=1, username="a", full_name="Old Name", city="X",
                     occupation="Student", photo_file_id="p", bio="b")
    db.register_user(user_id=1, username="a", full_name="New Name", city="Y",
                     occupation="Analyst", photo_file_id="p2", bio="b2")
    user = db.get_user(1)
    assert user["full_name"] == "New Name"
    assert user["status"] == "active"


def test_ban_and_unban(temp_db):
    db.register_user(user_id=2, username="b", full_name="Bob", city="Oslo",
                     occupation="Analyst", photo_file_id="p", bio="bio")
    assert db.set_user_status(2, "banned") is True
    assert db.get_user(2)["status"] == "banned"
    assert db.count_users("banned") == 1
    db.set_user_status(2, "active")
    assert db.get_user(2)["status"] == "active"


def test_task_and_submission_counts(temp_db):
    db.register_user(user_id=3, username="c", full_name="Cara", city="Lima",
                     occupation="Student", photo_file_id="p", bio="bio")
    task_id = db.create_task(title="Pandas Basics", description="Do the exercises.")
    assert db.get_task(task_id)["title"] == "Pandas Basics"

    db.add_submission(user_id=3, task_id=task_id, kind="link", content="https://github.com/x/y")
    db.add_submission(user_id=3, task_id=task_id, kind="text", content="my solution")
    per_task = db.count_submissions_per_task()
    assert per_task[0]["submissions"] == 2
    assert db.count_pending_submissions() == 2


def test_csv_exports(temp_db):
    from bot.utils import build_submissions_csv, build_users_csv

    db.register_user(user_id=4, username="d", full_name="Dan", city="Rome",
                     occupation="Student", photo_file_id="p", bio="bio<b>")
    task_id = db.create_task(title="SQL Joins", description="Practice joins.")
    db.add_submission(user_id=4, task_id=task_id, kind="text", content="SELECT 1;")

    users_csv = build_users_csv(db.iter_users_for_export()).decode("utf-8-sig")
    assert "Dan" in users_csv
    subs_csv = build_submissions_csv(db.iter_submissions_for_export()).decode("utf-8-sig")
    assert "SQL Joins" in subs_csv


def test_delete_user_cascades_submissions(temp_db):
    """Regression: delete_user used to hit the FK constraint and raise."""
    db.register_user(user_id=5, username="e", full_name="Eve", city="Paris",
                     occupation="Analyst", photo_file_id="p", bio="bio")
    task_id = db.create_task(title="T", description="desc")
    db.add_submission(user_id=5, task_id=task_id, kind="text", content="sol")

    assert db.delete_user(5) is True
    assert db.get_user(5) is None
    assert db.count_submissions(user_id=5) == 0
    # task and unrelated users are untouched
    assert db.get_task(task_id) is not None
    assert db.delete_user(999) is False


def test_delete_task_cascades_submissions(temp_db):
    db.register_user(user_id=6, username="f", full_name="Fay", city="Dublin",
                     occupation="Student", photo_file_id="p", bio="bio")
    task_id = db.create_task(title="T2", description="desc")
    db.add_submission(user_id=6, task_id=task_id, kind="text", content="sol")

    assert db.delete_task(task_id) is True
    assert db.get_task(task_id) is None
    assert db.count_submissions(task_id=task_id) == 0
