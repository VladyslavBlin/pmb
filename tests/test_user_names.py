from __future__ import annotations

from pmb.reasoning.user_names import detect_user_names, has_self_marker


def test_detect_user_names_from_assistant_style_facts():
    names = detect_user_names([
        "Користувача звати Влад",
        "Пользователя зовут Алексей",
        "User is named Alex",
    ])

    assert {"влад", "алексей", "alex"} <= names


def test_has_self_marker_covers_ukrainian_first_person():
    assert has_self_marker("Мені подобається працювати з локальними моделями")
