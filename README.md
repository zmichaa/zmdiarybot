# Telegram Homework Bot

Этот бот позволяет записывать и просматривать домашние задания в Telegram.

## Установка

1. Клонируйте репозиторий:
   
```bash
git clone https://github.com/zmichaa/zmdiarybot.git
```

2. Перейдите в папку с ботом:
   
```bash
cd zmdiarybot
```

3. Установите зависимости:
   
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

4. Создайте файл конфигурации `config.py`:

```env
TOKEN="ВАШ_ТОКЕН_БОТА"
ADMIN_CHAT_ID="ID_АДМИНА"
```

5. Запустите бота:

```bash
python bot.py
```

