from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.github.assets import GitHubAssetUploader
from app.telegram.handlers.newissue import NI_ENTER_LABELS, ni_enter_body


@pytest.fixture
def asset_uploader():
    return GitHubAssetUploader()


def test_upload_issue_image_user_attachment_success(asset_uploader):
    mock_repo = MagicMock()
    mock_repo.id = 12345678

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"url": "https://github.com/user-attachments/assets/test-uuid"}

    with (
        patch("app.github.assets.github_client.get_repo", return_value=mock_repo),
        patch("app.github.assets.get_raw_github_token", return_value="fake-token"),
        patch("httpx.Client.post", return_value=mock_resp),
    ):
        url = asset_uploader.upload_issue_image(
            repo_full_name="owner/repo",
            file_bytes=b"fake-image-bytes",
            filename="screenshot.png",
            mime_type="image/png",
        )

        assert url == "https://github.com/user-attachments/assets/test-uuid"


def test_upload_issue_image_fallback_to_repo_commit(asset_uploader):
    mock_repo = MagicMock()
    mock_repo.id = 12345678
    mock_repo.default_branch = "main"

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with (
        patch("app.github.assets.github_client.get_repo", return_value=mock_repo),
        patch("app.github.assets.get_raw_github_token", return_value="fake-token"),
        patch("httpx.Client.post", return_value=mock_resp),
    ):
        url = asset_uploader.upload_issue_image(
            repo_full_name="owner/repo",
            file_bytes=b"fake-image-bytes",
            filename="screenshot.png",
            mime_type="image/png",
        )

        assert url.startswith("https://github.com/owner/repo/raw/main/.github/assets/issue_images/")
        mock_repo.create_file.assert_called_once()
        call_kwargs = mock_repo.create_file.call_args.kwargs
        assert "[skip ci]" in call_kwargs["message"]
        assert call_kwargs["branch"] == "main"


@pytest.mark.asyncio
async def test_ni_enter_body_with_photo_and_caption():
    update = MagicMock()
    context = MagicMock()
    context.user_data = {"ni_repo": "owner/repo"}

    photo_item = MagicMock()
    photo_item.file_id = "photo-123"
    update.message.photo = [photo_item]
    update.message.caption = "This is a bug screenshot"
    update.message.document = None

    status_msg = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)

    fake_tg_file = AsyncMock()
    fake_tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"image-data"))
    context.bot.get_file = AsyncMock(return_value=fake_tg_file)

    with patch(
        "app.telegram.handlers.newissue.asset_uploader.upload_issue_image",
        return_value="https://github.com/user-attachments/assets/xyz",
    ):
        state = await ni_enter_body(update, context)

    assert state == NI_ENTER_LABELS
    expected_body = "This is a bug screenshot\n\n![Screenshot](https://github.com/user-attachments/assets/xyz)"
    assert context.user_data["ni_body"] == expected_body
    status_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_ni_enter_body_with_photo_no_caption():
    update = MagicMock()
    context = MagicMock()
    context.user_data = {"ni_repo": "owner/repo"}

    photo_item = MagicMock()
    photo_item.file_id = "photo-123"
    update.message.photo = [photo_item]
    update.message.caption = None
    update.message.document = None

    status_msg = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)

    fake_tg_file = AsyncMock()
    fake_tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"image-data"))
    context.bot.get_file = AsyncMock(return_value=fake_tg_file)

    with patch(
        "app.telegram.handlers.newissue.asset_uploader.upload_issue_image",
        return_value="https://github.com/user-attachments/assets/xyz",
    ):
        state = await ni_enter_body(update, context)

    assert state == NI_ENTER_LABELS
    assert context.user_data["ni_body"] == "![Screenshot](https://github.com/user-attachments/assets/xyz)"


@pytest.mark.asyncio
async def test_ni_enter_body_with_plain_text():
    update = MagicMock()
    context = MagicMock()
    context.user_data = {"ni_repo": "owner/repo"}

    update.message.photo = []
    update.message.document = None
    update.message.text = "Just a text description"
    update.message.reply_text = AsyncMock()

    state = await ni_enter_body(update, context)

    assert state == NI_ENTER_LABELS
    assert context.user_data["ni_body"] == "Just a text description"
