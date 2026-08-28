from __future__ import annotations

import asyncio
import html
import os
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()

Backend = Literal["comfyui", "vllm"]


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def csv_ints(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


TOKEN = env("TELEGRAM_BOT_TOKEN")
ADMINS = csv_ints(env("ADMIN_USER_IDS"))
CONTROL_MODE = env("CONTROL_MODE", "local").lower()
SERVER_HOST = env("SERVER_HOST", "127.0.0.1")
SERVER_PORT = env("SERVER_PORT", "22")
SERVER_USER = env("SERVER_USER", "")
SSH_KEY_PATH = os.path.expanduser(env("SSH_KEY_PATH", ""))
SSH_BIN = env("SSH_BIN", "/usr/bin/ssh")
SSH_STRICT_HOST_KEY_CHECKING = env("SSH_STRICT_HOST_KEY_CHECKING", "yes")
# Backwards-compatible alias for older configs.
SSH_TARGET = env("SSH_TARGET", "")
SYSTEMCTL_CMD = env("SYSTEMCTL_CMD", "sudo -n systemctl")
JOURNALCTL_CMD = env("JOURNALCTL_CMD", "sudo -n journalctl")
LOG_LINES = max(5, min(int(env("LOG_LINES", "30") or "30"), 100))
EXPOSURE_MODE = env("EXPOSURE_MODE", "local").lower()
EXPOSURE_LOCAL_CMD = env("EXPOSURE_LOCAL_CMD")
EXPOSURE_PUBLIC_CMD = env("EXPOSURE_PUBLIC_CMD")
EXPOSURE_STATUS_CMD = env("EXPOSURE_STATUS_CMD")
LOCAL_BIND_HOST = env("LOCAL_BIND_HOST", "127.0.0.1")
PUBLIC_BIND_HOST = env("PUBLIC_BIND_HOST", "0.0.0.0")

SERVICES: dict[Backend, str] = {
    "comfyui": env("COMFYUI_SERVICE", "comfyui.service"),
    "vllm": env("VLLM_SERVICE", "vllm.service"),
}
LABELS: dict[Backend, str] = {
    "comfyui": "🎨 ComfyUI · Krea",
    "vllm": "🧠 vLLM · Qwen",
}
URLS: dict[Backend, str] = {
    "comfyui": env("COMFYUI_URL", "http://127.0.0.1:8188"),
    "vllm": env("VLLM_URL", "http://127.0.0.1:8000"),
}

if EXPOSURE_MODE not in {"local", "public"}:
    EXPOSURE_MODE = "local"


@dataclass
class CmdResult:
    code: int
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return (self.stdout or self.stderr).strip()


class ControlPlane:
    """Small, whitelisted control layer. No Telegram text is ever interpolated into commands."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()

    async def run(self, command: str, timeout: float = 45) -> CmdResult:
        if CONTROL_MODE == "ssh":
            destination = SSH_TARGET or (f"{SERVER_USER}@{SERVER_HOST}" if SERVER_USER else SERVER_HOST)
            argv = [SSH_BIN, "-o", "BatchMode=yes"]
            if SERVER_PORT:
                argv.extend(["-p", SERVER_PORT])
            if SSH_KEY_PATH:
                argv.extend(["-i", SSH_KEY_PATH])
            if SSH_STRICT_HOST_KEY_CHECKING:
                argv.extend(["-o", f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY_CHECKING}"])
            argv.extend([destination, "--", command])
        else:
            argv = ["bash", "-lc", command]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            return CmdResult(process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace"))
        except asyncio.TimeoutError:
            process.kill()
            return CmdResult(124, "", "command timed out")
        except OSError as exc:
            return CmdResult(127, "", str(exc))

    async def service_state(self, backend: Backend) -> str:
        service = SERVICES[backend]
        if not service:
            return "not configured"
        result = await self.run(f"{SYSTEMCTL_CMD} is-active {shlex.quote(service)}", timeout=15)
        return result.text.splitlines()[-1] if result.text else "unknown"

    async def switch(self, backend: Backend) -> tuple[bool, str]:
        async with self.lock:
            other: Backend = "vllm" if backend == "comfyui" else "comfyui"
            steps: list[str] = []
            for item in (other, backend):
                service = SERVICES[item]
                if not service:
                    continue
                action = "start" if item == backend else "stop"
                result = await self.run(f"{SYSTEMCTL_CMD} {action} {shlex.quote(service)}")
                if result.code != 0 and action == "start":
                    return False, f"Не удалось запустить {LABELS[item]}:\n{result.text[-1200:] or 'без вывода'}"
                steps.append(f"{action} {service}: {'ok' if result.code == 0 else 'already stopped'}")
            state = await self.service_state(backend)
            return state == "active", "\n".join(steps + [f"Итог: {state}"])

    async def stop_all(self) -> str:
        async with self.lock:
            lines: list[str] = []
            for backend in ("comfyui", "vllm"):
                service = SERVICES[backend]
                if not service:
                    continue
                result = await self.run(f"{SYSTEMCTL_CMD} stop {shlex.quote(service)}")
                lines.append(f"{service}: {'ok' if result.code == 0 else result.text or 'already stopped'}")
            return "\n".join(lines)

    async def logs(self, backend: Backend) -> str:
        service = SERVICES[backend]
        if not service:
            return "Сервис не настроен."
        result = await self.run(
            f"{JOURNALCTL_CMD} -u {shlex.quote(service)} -n {LOG_LINES} --no-pager",
            timeout=20,
        )
        return result.text[-6000:] or "Логи пустые."

    async def exposure_status(self) -> str:
        if EXPOSURE_STATUS_CMD:
            result = await self.run(EXPOSURE_STATUS_CMD, timeout=15)
            value = result.text.lower()
            if "public" in value:
                return "public"
            if "local" in value:
                return "local"
        return EXPOSURE_MODE

    async def set_exposure(self, mode: Literal["local", "public"]) -> tuple[bool, str]:
        command = EXPOSURE_PUBLIC_CMD if mode == "public" else EXPOSURE_LOCAL_CMD
        if not command:
            return False, (
                "Команда режима не настроена. Добавь в .env:\n"
                f"EXPOSURE_{mode.upper()}_CMD=...\n"
                "Команда должна переключать bind/firewall и при необходимости перезапускать сервисы."
            )
        command = (
            command.replace("{mode}", mode)
            .replace("{local_bind}", shlex.quote(LOCAL_BIND_HOST))
            .replace("{public_bind}", shlex.quote(PUBLIC_BIND_HOST))
        )
        result = await self.run(command, timeout=60)
        if result.code != 0:
            return False, result.text[-1500:] or "команда завершилась с ошибкой"
        return True, result.text[-1500:] or f"Режим переключён на {mode}."

    async def gpu(self) -> str:
        result = await self.run(
            "nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total "
            "--format=csv,noheader,nounits",
            timeout=15,
        )
        if result.code != 0 or not result.text:
            return "GPU: nvidia-smi недоступен"
        name, temp, util, used, total = [x.strip() for x in result.text.splitlines()[0].split(",")]
        return f"GPU: {name}\n🌡 {temp}°C  ·  ⚡ {util}%\n🧠 VRAM: {used}/{total} MiB"


control = ControlPlane()
router = Router()


def allowed(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in ADMINS


def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 Krea / ComfyUI", callback_data="switch:comfyui"),
                InlineKeyboardButton(text="🧠 Qwen / vLLM", callback_data="switch:vllm"),
            ],
            [
                InlineKeyboardButton(text="📊 Статус", callback_data="status"),
                InlineKeyboardButton(text="📜 Логи", callback_data="logs:menu"),
            ],
            [
                InlineKeyboardButton(text="🏠 Local only", callback_data="exposure:local"),
                InlineKeyboardButton(text="🌍 Public", callback_data="exposure:public"),
            ],
            [InlineKeyboardButton(text="⏹ Остановить всё", callback_data="stop:ask")],
        ]
    )


def confirm_keyboard(backend: Backend) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ Запустить {LABELS[backend]}", callback_data=f"confirm:{backend}"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


async def dashboard() -> str:
    comfy, vllm, gpu, exposure = await asyncio.gather(
        control.service_state("comfyui"),
        control.service_state("vllm"),
        control.gpu(),
        control.exposure_status(),
    )
    def mark(value: str) -> str:
        return "🟢" if value == "active" else "⚪"
    return (
        "<b>AI Inference Control</b>\n"
        "<i>локальная панель управления сервером</i>\n\n"
        f"{mark(comfy)} {html.escape(LABELS['comfyui'])}: <code>{html.escape(comfy)}</code>\n"
        f"{mark(vllm)} {html.escape(LABELS['vllm'])}: <code>{html.escape(vllm)}</code>\n\n"
        f"🌐 Доступ: <b>{'PUBLIC' if exposure == 'public' else 'LOCAL ONLY'}</b>\n"
        f"🛰 Управление: <code>{html.escape(CONTROL_MODE)}</code> → <code>{html.escape(SERVER_USER + '@' + SERVER_HOST if SERVER_USER else (SSH_TARGET or SERVER_HOST))}</code>\n\n"
        f"{html.escape(gpu)}\n\n"
        f"🕒 {datetime.now().astimezone().strftime('%d.%m.%Y %H:%M:%S')}"
    )


async def deny(message: Message) -> None:
    await message.answer("⛔ Доступ закрыт. Добавь свой Telegram ID в ADMIN_USER_IDS.")


@router.message(CommandStart())
async def start(message: Message) -> None:
    if not allowed(message.from_user.id if message.from_user else None):
        await deny(message)
        return
    await message.answer(await dashboard(), reply_markup=keyboard())


@router.message(Command("id"))
async def telegram_id(message: Message) -> None:
    """Convenience command for filling ADMIN_USER_IDS during first setup."""
    user_id = message.from_user.id if message.from_user else "unknown"
    await message.answer(f"Твой Telegram ID: <code>{user_id}</code>")


@router.message(Command("status"))
async def status_command(message: Message) -> None:
    if not allowed(message.from_user.id if message.from_user else None):
        await deny(message)
        return
    await message.answer(await dashboard(), reply_markup=keyboard())


@router.message(Command("logs"))
async def logs_command(message: Message) -> None:
    if not allowed(message.from_user.id if message.from_user else None):
        await deny(message)
        return
    await message.answer("Выбери сервис:", reply_markup=logs_keyboard())


def logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 ComfyUI", callback_data="logs:comfyui")],
            [InlineKeyboardButton(text="🧠 vLLM", callback_data="logs:vllm")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")],
        ]
    )


@router.callback_query()
async def callbacks(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Доступ закрыт", show_alert=True)
        return
    data = callback.data or ""
    if data == "status":
        await callback.message.edit_text(await dashboard(), reply_markup=keyboard())
        await callback.answer("Обновлено")
    elif data == "back":
        await callback.message.edit_text(await dashboard(), reply_markup=keyboard())
        await callback.answer()
    elif data == "logs:menu":
        await callback.message.edit_text("Выбери сервис:", reply_markup=logs_keyboard())
        await callback.answer()
    elif data.startswith("logs:"):
        backend = data.split(":", 1)[1]
        if backend not in SERVICES:
            await callback.answer("Неизвестный сервис", show_alert=True)
            return
        output = await control.logs(backend)  # type: ignore[arg-type]
        await callback.message.answer(
            f"<b>{html.escape(LABELS[backend])} · последние логи</b>\n<pre>{html.escape(output)}</pre>"
        )
        await callback.answer()
    elif data.startswith("switch:"):
        backend = data.split(":", 1)[1]
        if backend not in SERVICES:
            await callback.answer("Неизвестный backend", show_alert=True)
            return
        await callback.message.edit_text(
            f"Переключить inference на <b>{html.escape(LABELS[backend])}</b>?\n"
            "Текущий сервис будет остановлен.",
            reply_markup=confirm_keyboard(backend),
        )
        await callback.answer()
    elif data.startswith("exposure:"):
        mode = data.split(":", 1)[1]
        if mode not in {"local", "public"}:
            await callback.answer("Неизвестный режим", show_alert=True)
            return
        label = "🌍 Public" if mode == "public" else "🏠 Local only"
        confirm = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ Включить {label}", callback_data=f"exposure-confirm:{mode}")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
            ]
        )
        await callback.message.edit_text(
            f"Переключить доступ к inference на <b>{label}</b>?\n"
            "Команда из .env применит bind/firewall-настройки.",
            reply_markup=confirm,
        )
        await callback.answer()
    elif data.startswith("exposure-confirm:"):
        global EXPOSURE_MODE
        mode = data.split(":", 1)[1]
        if mode not in {"local", "public"}:
            await callback.answer("Неизвестный режим", show_alert=True)
            return
        await callback.message.edit_text("⏳ Переключаю сетевой режим…")
        ok, detail = await control.set_exposure(mode)  # type: ignore[arg-type]
        if ok:
            EXPOSURE_MODE = mode
        prefix = "✅ Режим изменён" if ok else "⚠️ Режим не изменён"
        await callback.message.edit_text(
            f"<b>{prefix}</b>\n<pre>{html.escape(detail)}</pre>\n\n{await dashboard()}",
            reply_markup=keyboard(),
        )
        await callback.answer()
    elif data.startswith("confirm:"):
        backend = data.split(":", 1)[1]
        await callback.message.edit_text(f"⏳ Переключаю на {html.escape(LABELS[backend])}…")
        ok, detail = await control.switch(backend)  # type: ignore[arg-type]
        prefix = "✅ Готово" if ok else "⚠️ Проверь статус"
        await callback.message.edit_text(
            f"<b>{prefix}</b>\n<pre>{html.escape(detail)}</pre>\n\n{await dashboard()}",
            reply_markup=keyboard(),
        )
        await callback.answer()
    elif data == "stop:ask":
        confirm = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏹ Да, остановить", callback_data="stop:confirm")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
            ]
        )
        await callback.message.edit_text("Остановить ComfyUI и vLLM?", reply_markup=confirm)
        await callback.answer()
    elif data == "stop:confirm":
        await callback.message.edit_text("⏳ Останавливаю сервисы…")
        detail = await control.stop_all()
        await callback.message.edit_text(
            f"✅ Остановлено\n<pre>{html.escape(detail)}</pre>\n\n{await dashboard()}",
            reply_markup=keyboard(),
        )
        await callback.answer()
    elif data == "cancel":
        await callback.message.edit_text(await dashboard(), reply_markup=keyboard())
        await callback.answer("Отменено")


async def main() -> None:
    if not TOKEN or TOKEN.startswith("123456"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")
    if not ADMINS:
        print("WARNING: ADMIN_USER_IDS is empty; only /id will be useful until it is configured")
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
