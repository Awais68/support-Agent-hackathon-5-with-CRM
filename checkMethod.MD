# checkMethod — Identity Review Queue + Customer Merge ko test karne ka tareeqa

Ye file batati hai ke naya **identity review + merge** feature kya karta hai, isay
**akele (standalone)** kaise test karna hai, aur kisi **e-commerce platform** ke saath
jorr ke kaise test karna hai. Har step chhota hai — copy-paste karke chalao.

---

## 0. Ye feature hai kya (30 second summary)

Pehle system do customers ko sirf **naam milta-julta hone** par merge kar deta tha.
Ye galat tha, kyunke pg_trgm par measure kiya to pata chala:

| Naam ka pair | similarity | haqeeqat |
|---|---|---|
| Maria Garcia / Mario Garcia | 0.786 | **alag log** |
| Sarah Chen / Sarah Cheng | 0.769 | **alag log** |
| Muhammad Ali / Muhammad Alam | 0.688 | **alag log** |
| John Smith / Jon Smith | 0.615 | same person (typo) |

Yani har sacha typo kai jhoote matches se **neeche** score karta hai. Koi threshold
inko alag nahi kar sakta. Isliye ab:

- **Naam par auto-merge bilkul nahi hota.** Naya customer banta hai jis ki `metadata`
  me review flag likh diya jata hai.
- Ek **review queue** banti hai jise insaan dekh kar faisla karta hai.
- Faisla do me se ek hota hai: **merge** (ek hi banda hai) ya **dismiss** (alag log hain).

Teen naye endpoints:

| Method | Path | Kaam |
|---|---|---|
| `GET` | `/customers/review-queue` | Flagged possible duplicates ki list |
| `POST` | `/customers/{customer_id}/merge` | Source ko path waale customer me merge karo |
| `POST` | `/customers/{customer_id}/review/dismiss` | "Ye alag banda hai" — flag hata do |

> **Merge ki direction yaad rakho:** URL me jo `customer_id` hai wo **bachta** hai (target).
> Body me jo `source_customer_id` hai wo **khatam** ho jata hai — uske tickets, messages,
> agent_runs aur identifiers target par shift ho jate hain, phir uski row delete ho jati hai.
> Source ka purana email target ke saath identifier ki tarah jurra rehta hai, taake purane
> address par aane wala reply bhi sahi customer par land kare.

---

# HISSA 1 — Akele (standalone) test

## Step 1.1 — Infra start karo

```bash
cd "<project root>"
docker compose up -d postgres kafka zookeeper
```

Health check karo (postgres port **5433** par hai, 5432 par nahi):

```bash
docker compose ps
```

`techflow-postgres` aur `techflow-kafka` dono `healthy` hone chahiye. Agar kafka
crash-loop me ho (`NodeExistsException`), to stale znode ki wajah hai:

```bash
docker compose rm -sf zookeeper kafka && docker compose up -d zookeeper kafka
```

## Step 1.2 — Database extensions banao

Fuzzy matching `pg_trgm` aur `fuzzystrmatch` dono par depend karti hai:

```bash
docker compose exec -T postgres psql -U techflow -d techflow \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  -c "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;"
```

Ya seedha `./scripts/setup_e2e.sh` chala do — wo ye khud kar deta hai.

## Step 1.3 — API server start karo

```bash
export DATABASE_URL="postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost:5433/techflow"
export API_KEY="test-key-12345"
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8010
```

Doosre terminal me confirm karo:

```bash
curl -s http://127.0.0.1:8010/health
# {"status":"healthy","db":"ok","kafka":"ok"}
```

Agar `db` `ok` nahi hai to `DATABASE_URL` ka port check karo — **5433**.

## Step 1.4 — Do milte-julte naam waale customers banao

Ye webform se nahi hota (webform sirf email se match karta hai). Review flag
`get_customer_or_create_by_identifier` lagata hai, jo email/whatsapp/voice workers
use karte hain. Isliye seedha wahi function chalao:

```bash
DATABASE_URL="postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost:5433/techflow" \
.venv/bin/python - <<'PY'
import asyncio, os, random, asyncpg
from database import queries as db

async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"],
                                     init=db.init_pgvector_connection)
    n = random.randint(10**6, 10**7)          # har run me fresh data
    a = await db.get_customer_or_create_by_identifier(
        pool, "email", f"zayn.malik{n}@corpa.com", name=f"Zayn Malik {n}")
    b = await db.get_customer_or_create_by_identifier(
        pool, "email", f"zayn.malick{n}@corpb.com", name=f"Zayn Malick {n}")
    for cust, subj in ((a, "Order not delivered"), (b, "Refund request")):
        await db.create_ticket(pool, cust["email"], subj, channel="email",
                               customer_id=cust["id"], initial_message="test")
    print("TARGET", a["id"], a["email"])
    print("SOURCE", b["id"], b["email"])
    await pool.close()

asyncio.run(main())
PY
```

Output me `possible_duplicate_customer` log line dikhni chahiye aur do **alag** UUID.
Do UUIDs note kar lo — agli steps me chahiye.

**Yahan pehla assertion hai:** dono IDs alag hain. Purane code me ye ek hi ban jate
(similarity ≈ 0.77) — yani ek banda doosre ki poori ticket history dekh leta.

## Step 1.5 — Review queue dekho

```bash
curl -s "http://127.0.0.1:8010/customers/review-queue?limit=10" \
  -H "X-API-Key: test-key-12345" | python3 -m json.tool
```

Expected: `Zayn Malick` wali entry, uske saath `candidate_id` = `Zayn Malik`,
`similarity` ≈ 0.77, aur dono taraf ka `ticket_count`. Ye teen cheezein isliye hain
ke review karne wale ko decide karne ke liye kaafi context mile.

## Step 1.6 — Merge karo

`TARGET` aur `SOURCE` apne output se bharo:

```bash
TARGET=<step 1.4 ka pehla UUID>
SOURCE=<step 1.4 ka doosra UUID>

curl -s -X POST "http://127.0.0.1:8010/customers/$TARGET/merge" \
  -H "X-API-Key: test-key-12345" -H 'Content-Type: application/json' \
  -d "{\"source_customer_id\":\"$SOURCE\",\"reason\":\"same person, surname typo\"}" \
  | python3 -m json.tool
```

Expected response me:

```json
"moved": { "tickets": 1, "messages": 1, "agent_runs": 0, "customer_identifiers": 1 }
```

## Step 1.7 — DB me khud verify karo (sirf API par bharosa mat karo)

```bash
docker compose exec -T postgres psql -U techflow -d techflow \
  -c "SELECT count(*) AS source_should_be_0 FROM customers WHERE id='$SOURCE';" \
  -c "SELECT ticket_number, subject FROM tickets WHERE customer_id='$TARGET';" \
  -c "SELECT identifier_type, identifier_value FROM customer_identifiers WHERE customer_id='$TARGET';"
```

Teen cheezein check karo:

1. `source_should_be_0` = **0** → source row delete ho gayi
2. Tickets me **dono** subjects mojood hain → kuch data cascade me nahi mara
3. Identifiers me **dono** emails hain → purane address par reply ab bhi resolve hoga

Audit trail bhi dekho:

```bash
docker compose exec -T postgres psql -U techflow -d techflow \
  -c "SELECT metadata->'merged_from' FROM customers WHERE id='$TARGET';"
```

`merged_from` me source ka id, email, naam, waqt aur tumhara `reason` mehfooz hai.

## Step 1.8 — Dismiss wala raasta test karo

Agar dono waqai alag log hain:

```bash
curl -s -X POST "http://127.0.0.1:8010/customers/$SOURCE/review/dismiss" \
  -H "X-API-Key: test-key-12345" | python3 -m json.tool
```

Phir queue dobara dekho — wo entry ghayab honi chahiye, aur customer ki metadata me
`identity_reviewed_at` ka timestamp aa jayega (yani "dekh liya gaya tha, duplicate nahi tha").

## Step 1.9 — Error cases (ye zaroori hain, skip mat karo)

```bash
K="X-API-Key: test-key-12345"

# apne aap ko merge karna → 400
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:8010/customers/$TARGET/merge" -H "$K" \
  -H 'Content-Type: application/json' -d "{\"source_customer_id\":\"$TARGET\"}"

# source mojood hi nahi → 400 + saaf message
curl -s -X POST "http://127.0.0.1:8010/customers/$TARGET/merge" -H "$K" \
  -H 'Content-Type: application/json' \
  -d '{"source_customer_id":"00000000-0000-0000-0000-000000000000"}'

# unknown customer dismiss → 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:8010/customers/00000000-0000-0000-0000-000000000000/review/dismiss" -H "$K"

# API key ke baghair → 401
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8010/customers/review-queue"
```

Expected: `400`, `400 + {"error":"source customer ... not found"}`, `404`, `401`.

## Step 1.10 — Automated tests

```bash
.venv/bin/python -m pytest -q tests/test_identity_resolution.py
.venv/bin/python -m pytest -q                # poora unit suite
./scripts/setup_e2e.sh                       # Playwright e2e (asli Postgres + Kafka)
```

Expected: identity file me **33 passed**, poore suite me **124 passed, 12 skipped**,
e2e me **12 passed**.

## Step 1.11 — Fuzzy logic ka direct proof (optional, magar behtareen demo)

```bash
docker compose exec -T postgres psql -U techflow -d techflow -c "
SELECT a, b, round(similarity(a,b)::numeric,3) AS sim
FROM (VALUES
 ('Maria Garcia','Mario Garcia'),
 ('Sarah Chen','Sarah Cheng'),
 ('John Smith','Jon Smith')
) v(a,b) ORDER BY sim DESC;"
```

Ye output khud sabit kar deta hai ke naam se identity decide karna kyun na-mumkin hai:
sab se ooncha score do **alag** logon ka hai.

---

# HISSA 2 — E-commerce platform ke saath test

Idea ye hai: e-commerce store (Shopify / WooCommerce / custom Next.js store) se customer
support ka message aata hai. Ek hi grahak **do alag emails** se aata hai — ek order waqt
ka email, doosra support waqt ka. Purane code me naam ki wajah se ye ghalat merge ho jate
ya bilkul alag reh jate. Ab flow ye hai: **alag rehte hain → queue me flag hota hai →
insaan merge karta hai**.

## Step 2.1 — Store ki taraf se kya bhejna hai

Store se do raaste hain:

**(a) Webform channel** — jab store ka "Contact Support" form use karna ho:

```bash
curl -s -X POST http://127.0.0.1:8010/webhooks/webform \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "Ayesha Khan",
        "email": "ayesha.khan@gmail.com",
        "subject": "Order #10231 abhi tak nahi aaya",
        "message": "Maine 5 din pehle order kiya tha, tracking update nahi ho rahi.",
        "category": "technical"
      }'
```

> **Dhyan do:** `.test` / `.example` jaise reserved TLDs pydantic `EmailStr` reject kar deta
> hai (422 aata hai). Test data me `.com` use karo.

**(b) Identifier channel** — jab store ke paas customer ka phone/WhatsApp bhi ho
(cross-channel identity), tab workers `get_customer_or_create_by_identifier` chalate hain.
Yehi wo raasta hai jahan duplicate flag lagta hai.

## Step 2.2 — Ek grahak, do identities banao

Asli e-commerce scenario: checkout par kaam ka email diya, support par personal email:

```bash
DATABASE_URL="postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost:5433/techflow" \
.venv/bin/python - <<'PY'
import asyncio, os, asyncpg
from database import queries as db

async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"],
                                     init=db.init_pgvector_connection)
    # checkout ke waqt
    a = await db.get_customer_or_create_by_identifier(
        pool, "email", "a.khan@techmart.com", name="Ayesha Khan")
    await db.create_ticket(pool, a["email"], "Order #10231 delayed",
                           channel="email", customer_id=a["id"],
                           initial_message="Order 5 din se pending hai")
    # support ke waqt, personal email se
    b = await db.get_customer_or_create_by_identifier(
        pool, "email", "ayesha.khan@gmail.com", name="Ayesha Khan")
    await db.create_ticket(pool, b["email"], "Refund for order #10231",
                           channel="email", customer_id=b["id"],
                           initial_message="Refund chahiye")
    print("CHECKOUT_ID", a["id"])
    print("SUPPORT_ID ", b["id"])
    await pool.close()

asyncio.run(main())
PY
```

Yahan naam **bilkul same** hai (similarity 1.0) magar domain alag. System phir bhi
auto-merge **nahi** karega — bilkul theek, kyunke "Ayesha Khan" naam duniya me hazaron
logon ka ho sakta hai. Bas flag lag jayega.

## Step 2.3 — Support agent ka workflow simulate karo

Ye wo teen calls hain jo tumhari admin UI karegi:

```bash
K="X-API-Key: test-key-12345"

# 1. Agent ko review queue dikhao
curl -s "http://127.0.0.1:8010/customers/review-queue" -H "$K" | python3 -m json.tool

# 2. Agent dono ki history dekh kar tasdeeq karta hai
curl -s "http://127.0.0.1:8010/customers/a.khan@techmart.com/history" -H "$K" | python3 -m json.tool
curl -s "http://127.0.0.1:8010/customers/ayesha.khan@gmail.com/history" -H "$K" | python3 -m json.tool

# 3. Agent faisla karta hai: same order number hai → merge
curl -s -X POST "http://127.0.0.1:8010/customers/$CHECKOUT_ID/merge" -H "$K" \
  -H 'Content-Type: application/json' \
  -d "{\"source_customer_id\":\"$SUPPORT_ID\",\"reason\":\"Order #10231 dono tickets me same\"}" \
  | python3 -m json.tool
```

## Step 2.4 — Asal e-commerce assertion

Merge ke baad **dono** emails se history maangi jaye to **dono** tickets milne chahiye:

```bash
curl -s "http://127.0.0.1:8010/customers/ayesha.khan@gmail.com/history" \
  -H "X-API-Key: test-key-12345" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('tickets:', d['count'])
for t in d['tickets']:
    print(' -', t['ticket_number'], t['subject'])
"
```

Expected: `tickets: 2` — dono `Order #10231 delayed` aur `Refund for order #10231`.
Yahi merge ka asli faida hai: support agent ko ek hi jagah poori kahani milti hai,
grahak ko dobara sab kuch batana nahi parta.

## Step 2.5 — Store ki taraf se merge trigger karna (production pattern)

Agar tumhare store ke paas **pakka** signal hai (same `customer_id` Shopify me, ya same
verified phone number), to insaani review ki zaroorat hi nahi — store seedha merge call
kar sakta hai:

```bash
curl -X POST "https://<api-host>/customers/$PRIMARY_ID/merge" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"source_customer_id":"'"$DUPLICATE_ID"'",
       "reason":"Shopify customer_id 8871234 dono par same"}'
```

**Asool:** merge tabhi karo jab koi **mazboot** signal ho — verified phone, platform ka
apna customer id, ya insaan ki tasdeeq. Sirf naam kaafi nahi. Yehi is poore feature ki
buniyaad hai.

## Step 2.6 — Store se ulta test: alag log merge na hon

```bash
DATABASE_URL="postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost:5433/techflow" \
.venv/bin/python - <<'PY'
import asyncio, os, asyncpg
from database import queries as db

async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"],
                                     init=db.init_pgvector_connection)
    a = await db.get_customer_or_create_by_identifier(
        pool, "email", "maria.g@shopmail.com", name="Maria Garcia")
    b = await db.get_customer_or_create_by_identifier(
        pool, "email", "mario.g@othermail.com", name="Mario Garcia")
    assert a["id"] != b["id"], "BUG: alag log merge ho gaye!"
    print("PASS — alag customers, similarity 0.786 ke bawajood")
    await pool.close()

asyncio.run(main())
PY
```

Phir queue me jao aur is pair par **dismiss** maaro — yani "ye alag log hain".
Isse tum dono raaste (merge + dismiss) cover kar lete ho.

---

## Tuning knobs (env variables)

| Variable | Default | Kaam |
|---|---|---|
| `EMAIL_FUZZY_MAX_DISTANCE` | `2` | Email ke local part me kitne typo qubool hain |
| `NAME_FUZZY_THRESHOLD` | `0.6` | Is se neeche naam ka match flag hi nahi hota |
| `NAME_FUZZY_AMBIGUITY_MARGIN` | `0.08` | Agar runner-up itna qareeb ho to match reject |

Ye sirf **flag lagne** ko control karte hain — merge ab bhi sirf explicit call se hota hai.
Yani inko galat set karna bhi data corrupt nahi kar sakta, sirf queue shor wali ya khaali
ho jayegi.

---

## Cleanup

```bash
# API band karo
lsof -ti:8010 | xargs -r kill

# test data hatao (sirf local DB par chalao!)
docker compose exec -T postgres psql -U techflow -d techflow -c "
DELETE FROM customers WHERE email LIKE 'zayn.%' OR email LIKE '%@corpa.com'
   OR email LIKE '%@corpb.com' OR email IN
   ('a.khan@techmart.com','ayesha.khan@gmail.com','maria.g@shopmail.com','mario.g@othermail.com');"
```

`customers` par sab FK `ON DELETE CASCADE` hain, is liye ek delete se uske tickets,
messages, agent_runs aur identifiers sab saaf ho jate hain.

---

## Quick checklist

- [ ] `/health` → `db: ok`, `kafka: ok`
- [ ] Milte-julte naam waale do customers **alag** bante hain
- [ ] Naye customer ki metadata me `needs_identity_review: true`
- [ ] `GET /customers/review-queue` me entry + candidate + dono ke ticket counts
- [ ] `POST /customers/{id}/merge` → `moved` counts sahi
- [ ] Merge ke baad source row **delete**, tickets target par, **dono** emails resolve
- [ ] `metadata.merged_from` me audit trail
- [ ] `POST /customers/{id}/review/dismiss` → queue se entry hat gayi
- [ ] Error cases: `400` self-merge, `400` missing source, `404` unknown, `401` no key
- [ ] `pytest -q` → 124 passed · `setup_e2e.sh` → 12 passed
