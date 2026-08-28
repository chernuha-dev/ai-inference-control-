# AI Inference Control

Telegram-панель для headless-сервера с двумя inference-бэкендами:

- 🎨 ComfyUI / Krea
- 🧠 vLLM / Qwen

Из Telegram доступны переключение сервисов (с подтверждением), режим сети Local/Public, статус systemd, `nvidia-smi`, последние логи и аварийная остановка обоих сервисов.

## Быстрый старт

```bash
cd ai-inference-control-
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполни в `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=токен_от_BotFather
ADMIN_USER_IDS=твой_числовой_telegram_id
```

`ADMIN_USER_IDS` обязателен: пустой список блокирует все управляющие кнопки. Для получения ID отправь боту `/id`.
Если ID неизвестен, бот всё равно запустится в безопасном режиме: отправь ему `/id`, впиши полученное число в `.env` и перезапусти.

## Подключение к серверу

По умолчанию пример настроен на SSH: укажи `SERVER_HOST`, `SERVER_USER`, `SERVER_PORT` и путь к приватному ключу в `.env`. Бот использует `ssh -o BatchMode=yes`, поэтому пароль в `.env` не нужен. Можно вместо этого задать `SSH_TARGET` — имя алиаса из `~/.ssh/config`.

Если бот запускается прямо на inference-сервере, поставь `CONTROL_MODE=local`.

## Управление сервисами

Бот ожидает `comfyui.service` и `vllm.service`; при необходимости измени имена в `.env`.

Для действий systemd бот использует `sudo -n`, то есть пароль в `.env` не хранится. Настрой для пользователя бота ограниченное правило `NOPASSWD` только на нужные сервисы либо запускай бота с подходящими правами.

## Local / Public

Кнопки `🏠 Local only` и `🌍 Public` вызывают `EXPOSURE_LOCAL_CMD` или `EXPOSURE_PUBLIC_CMD` на сервере. Это намеренно сделано через явные команды из `.env`: у разных серверов разные firewall, интерфейсы и systemd-аргументы. Пример для собственного проверенного скрипта:

```dotenv
EXPOSURE_LOCAL_CMD=sudo -n /usr/local/sbin/ai-inference-exposure local
EXPOSURE_PUBLIC_CMD=sudo -n /usr/local/sbin/ai-inference-exposure public
EXPOSURE_STATUS_CMD=/usr/local/sbin/ai-inference-exposure status
```

Не подставляй в эти переменные команды из Telegram и не открывай порты без firewall-правил. Для `PUBLIC` ограничь доступ хотя бы VPN, reverse-proxy или allowlist-ом.

```bash
python bot.py
```

## Важно

`.env` намеренно игнорируется git и не должен попадать в GitHub. Не вставляй токен бота в коммиты, скриншоты или логи.
