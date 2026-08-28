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

По умолчанию пример настроен на SSH: укажи `SERVER_HOST`, `SERVER_USER`, `SERVER_PORT` и путь к приватному ключу в `.env`. Бот использует `ssh -o BatchMode=yes`, поэтому пароль в `.env` не нужен. `SSH_STRICT_HOST_KEY_CHECKING=accept-new` принимает ключ только при первом подключении и продолжает блокировать изменившийся ключ; для production можно поставить `yes` и заранее заполнить `SSH_KNOWN_HOSTS`. Можно вместо IP задать `SSH_TARGET` — имя алиаса из `~/.ssh/config`.

Если бот запускается прямо на inference-сервере, поставь `CONTROL_MODE=local`. В этом режиме SSH не используется: команды выполняются через `LOCAL_SHELL` (по умолчанию `/bin/bash`) в окружении самого бота. Убедись, что пользователь бота имеет права на `systemctl`, `journalctl` и `nvidia-smi`; для `sudo` используй ограниченное правило `NOPASSWD` из шаблона.

Важно для Docker: `CONTROL_MODE=local` выполняет команды внутри контейнера. Если systemd и GPU-сервисы работают на хосте, запускай бота на хосте напрямую или оставь `CONTROL_MODE=ssh` (например, SSH на хост), иначе `systemctl` внутри контейнера не будет управлять хостовыми сервисами.

При запуске через `docker compose` примонтируй SSH-ключ и `known_hosts`: в `.env` укажи `SSH_KEY_HOST_PATH` и `SSH_KNOWN_HOSTS_HOST_PATH`. Готовый `compose.yaml` монтирует их внутрь контейнера как `/root/.ssh/id_ed25519` и `/root/.ssh/known_hosts`.

## Управление сервисами

Бот ожидает `comfyui.service` и `vllm.service`; при необходимости измени имена в `.env`.

Для действий systemd бот использует `sudo -n`, то есть пароль в `.env` не хранится. Настрой для пользователя бота ограниченное правило `NOPASSWD` только на нужные сервисы либо запускай бота с подходящими правами.

Готовый шаблон правила: `deploy/ai-inference-control.sudoers.example`. После замены `YOUR_SERVER_USER` проверь его через `visudo -cf` и положи в `/etc/sudoers.d/ai-inference-control`.

## Local / Public

Кнопки `🏠 Local only` и `🌍 Public` вызывают `EXPOSURE_LOCAL_CMD` или `EXPOSURE_PUBLIC_CMD` на сервере. Это намеренно сделано через явные команды из `.env`: у разных серверов разные firewall, интерфейсы и systemd-аргументы. Пример для собственного проверенного скрипта:

```dotenv
LOCAL_BIND_HOST=192.168.1.100
PUBLIC_BIND_HOST=0.0.0.0
EXPOSURE_LOCAL_CMD=sudo -n env AI_LOCAL_BIND={local_bind} AI_PUBLIC_BIND={public_bind} /usr/local/sbin/ai-inference-exposure local
EXPOSURE_PUBLIC_CMD=sudo -n env AI_LOCAL_BIND={local_bind} AI_PUBLIC_BIND={public_bind} /usr/local/sbin/ai-inference-exposure public
EXPOSURE_STATUS_CMD=sudo -n /usr/local/sbin/ai-inference-exposure status
```

Шаблоны `{local_bind}` и `{public_bind}` подставляются ботом из `.env`. Скрипт из `scripts/ai-inference-exposure` нужно один раз установить на сервер:

```bash
sudo install -m 0750 scripts/ai-inference-exposure /usr/local/sbin/ai-inference-exposure
```

Не подставляй в эти переменные команды из Telegram и не открывай порты без firewall-правил. Для `PUBLIC` ограничь доступ хотя бы VPN, reverse-proxy или allowlist-ом.

```bash
python bot.py
```

## Важно

`.env` намеренно игнорируется git и не должен попадать в GitHub. Не вставляй токен бота в коммиты, скриншоты или логи.
