# ResearchSurvey — ҷойгиркунӣ дар интернет

Ду роҳ ҳаст:

| Роҳ | Барои чӣ | Вақт | Нарх |
|---|---|---|---|
| **A. Render.com** (Blueprint) | санҷиш ва пурсиши озмоишӣ | ~10 дақиқа | нақшаи ройгон |
| **B. Ubuntu VPS** (nginx + gunicorn + PostgreSQL) | истифодаи доимӣ (production) | ~1 соат | аз нархи VPS |

---

## A. Render.com — роҳи осон барои оғози санҷиш

1. Дар <https://render.com> бо ҳисоби GitHub ворид шавед.
2. Тугмаи **Deploy to Render**-ро дар саҳифаи репозиторий пахш кунед
   (ё **New → Blueprint** → репозиторийи `Simple1414/pursishnoma`, шохаи `main`).
3. Render файли `render.yaml`-ро дар решаи репозиторий меёбад ва худкор месозад:
   - пойгоҳи маълумоти **PostgreSQL** (`researchsurvey-db`);
   - хидмати веб `researchsurvey` (Python, gunicorn, HTTPS).
4. Render ду қиматро мепурсад:
   - `DJANGO_ADMIN_USERNAME` — номи администратор, масалан `admin`;
   - `DJANGO_ADMIN_PASSWORD` — рамзи қавӣ (ҳадди ақал 10 аломат, на танҳо рақам).
5. **Apply**-ро пахш кунед. Пас аз 5–10 дақиқа суроға пайдо мешавад, масалан
   `https://researchsurvey.onrender.com`.
6. Ба `…/accounts/login/` ворид шавед → **Конструктори пурсишнома** → файли `.docx`-ро бор кунед →
   саволҳоро санҷед → **Тасдиқ кардан** → **Нашр кардан** → пайванд ё QR-кодро ба донишҷӯён диҳед.

**Пурсишномаҳои омода:** файлҳои `seed/*.docx` (рӯйхат дар `seed/manifest.json`) ҳангоми оғози барнома
худкор ҳамчун **тарҳ** ворид мешаванд (ҳар файл танҳо як маротиба). Онҳоро дар «Пурсишномаҳои ман» кушоед,
санҷед, **Тасдиқ кардан** ва **Нашр кардан**-ро пахш кунед. Пурсишномаи навро ҳамин тавр илова кардан мумкин:
файлро ба `seed/` гузоред ва дар `manifest.json` сатр илова кунед.

`DJANGO_SECRET_KEY` худкор тавлид мешавад; ҳеҷ калид ё рамз дар код нест.

**Маҳдудиятҳои нақшаи ройгони Render (муҳим):**
- Хидмати веб пас аз 15 дақиқаи бекорӣ «хоб» меравад; дархости аввал ~30–60 сония тӯл мекашад.
- PostgreSQL-и ройгон муҳлати маҳдуд дорад (ҳоло 30 рӯз). Барои пурсиши воқеӣ ба нақшаи пулакӣ гузаред
  ё пеш аз анҷоми муҳлат маълумотро содир кунед (Excel/CSV) ва `pg_dump` гиред.
- Диск муваққатӣ аст: файли аслии `.docx`, ки бор кардед, пас аз ҷойгиркунии нав нест мешавад.
  **Ҳамаи саволҳо ва ҷавобҳо дар PostgreSQL нигоҳ дошта мешаванд** ва гум намешаванд.

### Фаъол кардани ёрдамчии AI (ихтиёрӣ)
Дар Render → `researchsurvey` → **Environment**:
```
AI_PROVIDER=anthropic
AI_API_KEY=<калиди API-и Anthropic>
AI_MODEL=claude-opus-5-5
```
Бе ин танзимот платформа пурра кор мекунад; танҳо саҳифаи «Ёрдамчии илмии AI» ғайрифаъол мемонад.

---

## B. Ubuntu VPS (22.04 / 24.04) — production

```bash
# 1. Бастаҳо
sudo apt update && sudo apt install -y python3.12-venv python3-pip postgresql nginx certbot python3-certbot-nginx git
sudo adduser --system --group --home /srv/researchsurvey researchsurvey

# 2. Пойгоҳи маълумот
sudo -u postgres psql -c "CREATE USER researchsurvey WITH PASSWORD 'РАМЗИ_ҚАВӢ';"
sudo -u postgres psql -c "CREATE DATABASE researchsurvey OWNER researchsurvey;"

# 3. Код ва муҳит
sudo -u researchsurvey git clone https://github.com/Simple1414/pursishnoma.git /srv/researchsurvey/app
cd /srv/researchsurvey/app
sudo -u researchsurvey python3.12 -m venv /srv/researchsurvey/venv
sudo -u researchsurvey /srv/researchsurvey/venv/bin/pip install -r requirements.txt
sudo -u researchsurvey cp .env.example .env && sudo -u researchsurvey nano .env   # ҳамаи қиматҳоро пур кунед
sudo chmod 600 .env

# 4. Омодасозӣ
sudo -u researchsurvey bash -c 'set -a; . ./.env; set +a; \
  /srv/researchsurvey/venv/bin/python manage.py migrate && \
  /srv/researchsurvey/venv/bin/python manage.py collectstatic --noinput && \
  /srv/researchsurvey/venv/bin/python manage.py ensure_admin && \
  /srv/researchsurvey/venv/bin/python manage.py check --deploy'

# 5. gunicorn (systemd) ва nginx
sudo cp deploy/researchsurvey.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now researchsurvey
sudo cp deploy/nginx.conf /etc/nginx/sites-available/researchsurvey   # домейнро иваз кунед
sudo ln -s /etc/nginx/sites-available/researchsurvey /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 6. HTTPS (Let's Encrypt)
sudo certbot --nginx -d survey.example.org
```

### Навсозӣ
```bash
cd /srv/researchsurvey/app && sudo -u researchsurvey git pull
sudo -u researchsurvey /srv/researchsurvey/venv/bin/pip install -r requirements.txt
sudo -u researchsurvey bash -c 'set -a; . ./.env; set +a; /srv/researchsurvey/venv/bin/python manage.py migrate && /srv/researchsurvey/venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart researchsurvey
```

### Нусхаи эҳтиётӣ (backup)
```bash
sudo mkdir -p /var/backups/researchsurvey && sudo chown researchsurvey /var/backups/researchsurvey
# ҳар рӯз соати 02:30, 14 рӯз нигоҳ дошта мешавад
echo '30 2 * * * /srv/researchsurvey/app/deploy/backup_db.sh >> /var/log/researchsurvey-backup.log 2>&1' | sudo crontab -u researchsurvey -
```
Барқароркунӣ: `sudo systemctl stop researchsurvey && deploy/restore_db.sh <файл.dump>`.
Нусхаҳоро мунтазам ба сервери дигар нусхабардорӣ кунед ва барқароркуниро дар пойгоҳи санҷишӣ месанҷед.

### Амният — рӯйхати санҷиш
- `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY` дароз ва тасодуфӣ, `.env` бо ҳуқуқи `600`.
- `python manage.py check --deploy` бояд бе огоҳӣ бошад.
- `/media/` дар nginx баста аст; файлҳои бор кардашуда ба таври оммавӣ дастрас нестанд.
- Дастрасӣ ба PostgreSQL танҳо аз `localhost` (танзими пешфарзи Ubuntu).
- Журнали аудит: **Маъмурият → Амният**.
