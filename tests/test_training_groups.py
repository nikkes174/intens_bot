"""Tests for environment-managed Telegram training groups."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember
from environs import Env

from tgbot.config import TrainingGroups, load_config
from tgbot.config import logger as config_logger
from tgbot.db.seed import seed_products
from tgbot.services.access import AccessService, validate_training_groups


def _groups_env(
    monkeypatch, *, glute="-1001234567890", vshape="-1001234567891", core="-1001234567892"
):
    values = {
        "GLUTE_ID": glute,
        "V_SHARE_ID": vshape,
        "CORE_PROTOCOL_ID": core,
    }
    for env_name, value in values.items():
        monkeypatch.setenv(env_name, value)
    return Env()


def _write_env(path, *, glute="-1001234567890", vshape="-1001234567891", core="-1001234567892"):
    path.write_text(
        "\n".join(
            (
                "BOT_TOKEN=123:test",
                "ADMINS=123",
                f"GLUTE_ID={glute}",
                f"V_SHARE_ID={vshape}",
                f"CORE_PROTOCOL_ID={core}",
            )
        ),
        encoding="utf-8",
    )


def test_product_codes_resolve_to_the_required_environment_variables(monkeypatch):
    groups = TrainingGroups.from_env(_groups_env(monkeypatch))

    assert groups.get_training_chat_id("glute") == -1001234567890
    assert groups.get_training_chat_id("vshape") == -1001234567891
    assert groups.get_training_chat_id("core") == -1001234567892
    assert groups.get_training_chat_id("bundle_all") is None
    assert groups.get_training_product_code(-1001234567891) == "vshape"


def test_missing_environment_variable_disables_only_that_group(monkeypatch):
    with patch.object(config_logger, "warning") as warning:
        groups = TrainingGroups.from_env(
            _groups_env(monkeypatch, glute="", vshape="-1001234567891")
        )

    assert groups.get_training_chat_id("glute") is None
    assert groups.get_training_chat_id("vshape") == -1001234567891
    assert "is empty" in warning.call_args.args[0]
    assert warning.call_args.args[1] == "GLUTE_ID"


def test_invalid_and_duplicate_ids_are_rejected_independently(monkeypatch):
    with patch.object(config_logger, "error") as error:
        invalid = TrainingGroups.from_env(_groups_env(monkeypatch, glute="not-a-number"))
    assert invalid.get_training_chat_id("glute") is None
    assert "must contain a numeric" in error.call_args.args[0]
    assert error.call_args.args[1] == "GLUTE_ID"

    with patch.object(config_logger, "error") as error:
        duplicate = TrainingGroups.from_env(
            _groups_env(monkeypatch, glute="-1001234567890", vshape="-1001234567890")
        )
    assert duplicate.get_training_chat_id("glute") is None
    assert duplicate.get_training_chat_id("vshape") is None
    assert duplicate.get_training_chat_id("core") == -1001234567892
    assert duplicate.duplicate_chat_ids() == {-1001234567890: ("glute", "vshape")}
    assert "multiple products" in error.call_args.args[0]


def test_load_config_reads_group_ids_again_after_restart(tmp_path, monkeypatch):
    for env_name in ("GLUTE_ID", "V_SHARE_ID", "CORE_PROTOCOL_ID"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADMINS", raising=False)

    first_path = tmp_path / "first.env"
    _write_env(first_path, glute="-1001234567890")
    first = load_config(str(first_path))

    second_path = tmp_path / "second.env"
    _write_env(second_path, glute="-1001234567800")
    for env_name in ("GLUTE_ID", "V_SHARE_ID", "CORE_PROTOCOL_ID"):
        monkeypatch.delenv(env_name, raising=False)
    second = load_config(str(second_path))

    assert first.training_groups.get_training_chat_id("glute") == -1001234567890
    assert second.training_groups.get_training_chat_id("glute") == -1001234567800


@pytest.mark.asyncio
async def test_invites_use_env_id_instead_of_database_id(sample_user, training_repo, invite_repo):
    await seed_products()
    glute = await training_repo.get_by_code("glute")
    await training_repo.update_chat_id(glute.id, -1009999999999)
    groups = TrainingGroups(glute_id=-1001234567890)
    bot = AsyncMock()
    bot.create_chat_invite_link.return_value = SimpleNamespace(
        invite_link="https://t.me/+env-group"
    )

    invite = await AccessService(bot, groups).create_invite_link(
        sample_user.id, glute.id, training_repo, invite_repo
    )

    assert invite == "https://t.me/+env-group"
    assert bot.create_chat_invite_link.await_args.kwargs["chat_id"] == -1001234567890
    assert bot.create_chat_invite_link.await_args.kwargs["creates_join_request"] is True


@pytest.mark.asyncio
async def test_changed_config_after_restart_replaces_stale_invite(
    sample_user, training_repo, invite_repo
):
    await seed_products()
    glute = await training_repo.get_by_code("glute")
    bot = AsyncMock()
    bot.create_chat_invite_link.side_effect = [
        SimpleNamespace(invite_link="https://t.me/+old"),
        SimpleNamespace(invite_link="https://t.me/+new"),
    ]

    old_invite = await AccessService(
        bot, TrainingGroups(glute_id=-1001234567890)
    ).create_invite_link(sample_user.id, glute.id, training_repo, invite_repo)
    new_invite = await AccessService(
        bot, TrainingGroups(glute_id=-1001234567800)
    ).create_invite_link(sample_user.id, glute.id, training_repo, invite_repo)

    assert old_invite == "https://t.me/+old"
    assert new_invite == "https://t.me/+new"
    assert bot.revoke_chat_invite_link.await_args.kwargs["chat_id"] == -1001234567890
    assert bot.create_chat_invite_link.await_args.kwargs["chat_id"] == -1001234567800


@pytest.mark.asyncio
async def test_join_request_uses_env_mapping_and_telegram_user_access(
    sample_user, training_repo, access_repo
):
    await seed_products()
    glute = await training_repo.get_by_code("glute")
    await training_repo.update_chat_id(glute.id, -1009999999999)
    await access_repo.grant_access(sample_user.id, glute.id)
    service = AccessService(AsyncMock(), TrainingGroups(glute_id=-1001234567890))

    accepted = await service.handle_join_request(
        sample_user.id, -1001234567890, access_repo, training_repo
    )
    stale_db_group = await service.handle_join_request(
        sample_user.id, -1009999999999, access_repo, training_repo
    )

    assert accepted is True
    assert stale_db_group is False


@pytest.mark.asyncio
async def test_unpaid_or_foreign_group_request_is_ignored(sample_user, training_repo, access_repo):
    await seed_products()
    service = AccessService(AsyncMock(), TrainingGroups(glute_id=-1001234567890))

    unpaid = await service.handle_join_request(
        sample_user.id, -1001234567890, access_repo, training_repo
    )
    foreign = await service.handle_join_request(
        sample_user.id, -1001234567999, access_repo, training_repo
    )

    assert unpaid is False
    assert foreign is False


@pytest.mark.asyncio
async def test_duplicate_env_id_cannot_fall_back_to_legacy_database_group(
    sample_user, sample_training, training_repo, access_repo
):
    duplicate_chat_id = -1001234567890
    await training_repo.update_chat_id(sample_training.id, duplicate_chat_id)
    await access_repo.grant_access(sample_user.id, sample_training.id)
    groups = TrainingGroups(glute_id=duplicate_chat_id, v_share_id=duplicate_chat_id)
    service = AccessService(AsyncMock(), groups)

    approved = await service.handle_join_request(
        sample_user.id, duplicate_chat_id, access_repo, training_repo
    )

    assert approved is False


@pytest.mark.asyncio
async def test_startup_checks_join_request_link_rights_and_isolates_failures():
    groups = TrainingGroups(
        glute_id=-1001234567890,
        v_share_id=-1001234567891,
        core_protocol_id=-1001234567892,
    )
    bot = AsyncMock()
    bot.id = 12345
    bot.get_chat_member.side_effect = [
        TelegramBadRequest(
            method=GetChatMember(chat_id=-1001234567892, user_id=12345), message="no"
        ),
        SimpleNamespace(status="administrator", can_invite_users=True),
        SimpleNamespace(status="creator", can_invite_users=False),
    ]
    bot.create_chat_invite_link.return_value = SimpleNamespace(invite_link="https://t.me/+probe")

    problems = await validate_training_groups(bot, groups)

    assert len(problems) == 1
    assert "core" in problems[0]
    assert bot.get_chat_member.await_count == 3
    assert bot.create_chat_invite_link.await_count == 2
    assert all(
        call.kwargs["creates_join_request"] for call in bot.create_chat_invite_link.await_args_list
    )
    assert bot.revoke_chat_invite_link.await_count == 2
