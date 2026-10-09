"""Tests for cross-channel identity resolution via customer_identifiers table.

Also covers pg_trgm fuzzy matching identity resolution.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from database import queries as db

pytestmark = pytest.mark.asyncio


class TestFuzzySearchCustomers:
    """Test fuzzy search by name/email using pg_trgm."""

    async def test_fuzzy_search_by_email_found(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "id": UUID("11111111-1111-1111-1111-111111111111"),
                "email": "jon@example.com",
                "name": "Jon Doe",
                "similarity": 0.6,
            },
            {
                "id": UUID("22222222-2222-2222-2222-222222222222"),
                "email": "john@example.net",
                "name": "John Doe",
                "similarity": 0.35,
            },
        ]
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        results = await db.fuzzy_search_customers(
            mock_db_pool, "john@example.com", search_field="email", threshold=0.3
        )

        assert len(results) == 2
        assert results[0]["similarity"] >= 0.3

    async def test_fuzzy_search_email_below_threshold(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        results = await db.fuzzy_search_customers(
            mock_db_pool, "bob@example.com", search_field="email", threshold=0.8
        )

        assert len(results) == 0

    async def test_fuzzy_search_by_name(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "id": UUID("11111111-1111-1111-1111-111111111111"),
                "name": "Jon Doe",
                "email": "jon@example.com",
                "similarity": 0.55,
            },
        ]
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        results = await db.fuzzy_search_customers(
            mock_db_pool, "John Doe", search_field="name", threshold=0.3
        )

        assert len(results) == 1
        assert results[0]["similarity"] >= 0.3

    async def test_fuzzy_search_invalid_field(self, mock_db_pool):
        with pytest.raises(ValueError):
            await db.fuzzy_search_customers(mock_db_pool, "test", search_field="phone")

    async def test_fuzzy_search_empty_term(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        results = await db.fuzzy_search_customers(
            mock_db_pool, "", search_field="email", threshold=0.3
        )

        assert results == []


class TestFindCustomerByNameEmail:
    """Exact-email customer lookup (no fuzzy resolution, AUDIT S2)."""

    async def test_exact_email_match_returns_first(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "email": "john@example.com",
            "name": "John Doe",
            "company": "Acme Corp",
            "tier": "starter",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "metadata": {},
        }
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.find_customer_by_name_email(mock_db_pool, "john@example.com")

        assert result is not None
        assert result["email"] == "john@example.com"
        # Should NOT have called fuzzy query
        assert mock_conn.fetchrow.call_count == 1

    async def test_lookalike_email_is_not_returned(self, mock_db_pool):
        """No exact match → None; a similar email is a different person (S2)."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.find_customer_by_name_email(mock_db_pool, "john@example.com")

        assert result is None
        assert mock_conn.fetchrow.await_count == 1
        sql, value = mock_conn.fetchrow.await_args.args
        assert "levenshtein" not in sql and "similarity" not in sql
        assert value == "john@example.com"

    async def test_email_is_normalized(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        await db.find_customer_by_name_email(mock_db_pool, "  John@Example.COM ")

        assert mock_conn.fetchrow.await_args.args[1] == "john@example.com"

    async def test_name_is_never_used_to_match(self, mock_db_pool):
        """Same domain + similar name used to resolve to a customer; it must not."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.find_customer_by_name_email(
            mock_db_pool, "john.smith@acme.com", name="John Smith", company="Acme Corp"
        )

        assert result is None
        mock_conn.fetch.assert_not_awaited()


class TestGetCustomerByIdentifier:
    """Test get_customer_by_identifier resolves across channels."""

    async def test_find_by_email(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "email": "alice@acmecorp.com",
            "name": "Alice Johnson",
            "company": "Acme Corp",
            "tier": "enterprise",
        }
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.get_customer_by_identifier(mock_db_pool, "email", "alice@acmecorp.com")

        assert result is not None
        assert result["email"] == "alice@acmecorp.com"

    async def test_find_by_phone(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "email": "alice@acmecorp.com",
            "name": "Alice Johnson",
            "tier": "enterprise",
        }
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.get_customer_by_identifier(mock_db_pool, "phone", "+14155551234")

        assert result is not None
        assert result["email"] == "alice@acmecorp.com"

    async def test_find_by_web_session(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "email": "alice@acmecorp.com",
            "name": "Alice Johnson",
            "tier": "enterprise",
        }
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.get_customer_by_identifier(
            mock_db_pool, "web_session", "session-abc-123-alice"
        )

        assert result is not None
        assert result["email"] == "alice@acmecorp.com"

    async def test_not_found_returns_none(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.get_customer_by_identifier(mock_db_pool, "email", "nonexistent@test.com")

        assert result is None


class TestAddCustomerIdentifier:
    """Test adding identifiers to customers."""

    async def test_add_new_identifier(self, mock_db_pool):
        customer_id = UUID("11111111-1111-1111-1111-111111111111")

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": UUID("22222222-2222-2222-2222-222222222222"),
            "customer_id": customer_id,
            "identifier_type": "phone",
            "identifier_value": "+14155559999",
            "created_at": datetime.now(UTC),
        }
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.add_customer_identifier(
            mock_db_pool, customer_id, "phone", "+14155559999"
        )

        assert result["identifier_type"] == "phone"
        assert result["identifier_value"] == "+14155559999"

    async def test_add_duplicate_identifier(self, mock_db_pool):
        customer_id = UUID("11111111-1111-1111-1111-111111111111")

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # ON CONFLICT DO NOTHING returns None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        result = await db.add_customer_identifier(
            mock_db_pool, customer_id, "phone", "+14155551234"
        )

        assert "error" in result


class TestGetCustomerOrCreateByIdentifier:
    """Test find-or-create logic with identifier resolution."""

    def _setup_conn_with_transaction(self, mock_conn):
        mock_conn.__aenter__.return_value = mock_conn
        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)

    async def test_existing_email_returns_customer(self, mock_db_pool):
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetchrow.return_value = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "email": "alice@acmecorp.com",
            "name": "Alice Johnson",
            "company": "Acme Corp",
            "tier": "enterprise",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "metadata": {},
        }

        result = await db.get_customer_or_create_by_identifier(
            mock_db_pool, "email", "alice@acmecorp.com"
        )

        assert result["email"] == "alice@acmecorp.com"

    async def test_new_identifier_creates_customer(self, mock_db_pool):
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact identifier → not found
            None,  # Step 1b: exact customers.email → not found
            None,  # Step 2: similar email (review hint) → none
            None,  # Step 3: similar name (review hint) → none
            {  # Step 4: INSERT customers RETURNING
                "id": UUID("33333333-3333-3333-3333-333333333333"),
                "email": "newuser@test.com",
                "name": "New User",
                "company": None,
                "tier": "starter",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        result = await db.get_customer_or_create_by_identifier(
            mock_db_pool, "email", "newuser@test.com", name="New User"
        )

        assert result["email"] == "newuser@test.com"
        assert result["name"] == "New User"
        assert result["tier"] == "starter"

    async def test_similar_email_creates_new_customer_flagged_for_review(self, mock_db_pool):
        """alice1@ vs alice@ (S2): a new customer with a review hint, never a link."""
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        matched_id = UUID("66666666-6666-6666-6666-666666666666")
        new_id = UUID("aaaaaaaa-6666-6666-6666-666666666666")
        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact identifier → not found
            None,  # Step 1b: exact customers.email → not found
            {"id": matched_id, "name": "Jon Doe", "sim": 0.6},  # Step 2: similar email
            {  # Step 4: INSERT new customer
                "id": new_id,
                "email": "john@example.com",
                "name": "John Doe",
                "company": None,
                "tier": "starter",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        result = await db.get_customer_or_create_by_identifier(
            mock_db_pool, "email", "john@example.com", name="John Doe"
        )

        assert result["id"] == new_id
        metadata_json = mock_conn.fetchrow.await_args_list[-1].args[-1]
        assert str(matched_id) in metadata_json
        assert "needs_identity_review" in metadata_json
        # Nothing was linked to the similar customer.
        for call in mock_conn.execute.await_args_list:
            assert matched_id not in call.args

    async def test_name_match_never_links_identifier(self, mock_db_pool):
        """A similar name must create a NEW customer flagged for review, not link."""
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        candidate_id = UUID("77777777-7777-7777-7777-777777777777")
        new_id = UUID("aaaaaaaa-7777-7777-7777-777777777777")
        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact identifier → not found
            None,  # Step 1b: exact customers.email → not found
            None,  # Step 2: similar email → none
            {  # Step 3: similar name found → review flag only
                "id": candidate_id,
                "name": "Jon Doe",
                "sim": 0.75,
            },
            {  # Step 4: INSERT new customer
                "id": new_id,
                "email": "new-email@test.com",
                "name": "John Doe",
                "company": None,
                "tier": "starter",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        result = await db.get_customer_or_create_by_identifier(
            mock_db_pool, "email", "new-email@test.com", name="John Doe"
        )

        # New customer, NOT the similarly-named existing one
        assert result["id"] == new_id
        # The duplicate hint was persisted on the INSERT
        insert_call = mock_conn.fetchrow.await_args_list[-1]
        metadata_json = insert_call.args[-1]
        assert str(candidate_id) in metadata_json
        assert "needs_identity_review" in metadata_json

    async def test_similarity_threshold_is_applied_in_sql(self, mock_db_pool):
        """The review-hint threshold is a SQL filter, so low matches never come back."""
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        new_id = UUID("88888888-8888-8888-8888-888888888888")
        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact identifier → not found
            None,  # Step 1b: exact customers.email → not found
            None,  # Step 2: similar email above threshold → none
            None,  # Step 3: similar name → none
            {  # Step 4: INSERT new customer
                "id": new_id,
                "email": "nobody@example.com",
                "name": "New Person",
                "company": None,
                "tier": "starter",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        result = await db.get_customer_or_create_by_identifier(
            mock_db_pool,
            "email",
            "nobody@example.com",
            name="New Person",
            fuzzy_threshold=0.8,
        )

        assert result["id"] == new_id
        email_hint_call = mock_conn.fetchrow.await_args_list[2]
        assert email_hint_call.args[-1] == 0.8


class TestLinkIdentifiers:
    """Test linking multiple identifiers to a single customer."""

    async def test_link_phone_and_session(self, mock_db_pool):
        customer_id = UUID("11111111-1111-1111-1111-111111111111")
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        await db.link_identifiers(
            mock_db_pool,
            customer_id=customer_id,
            identifiers=[("phone", "+14155551234"), ("web_session", "sess-001")],
        )

        assert mock_conn.execute.call_count == 2


class TestGetCustomerHistory:
    """Test cross-channel history retrieval."""

    async def test_history_by_email_includes_cross_channel(self, mock_db_pool):
        mock_conn = AsyncMock()
        # First query finds customer_id via identifier table
        mock_conn.fetchval.return_value = UUID("11111111-1111-1111-1111-111111111111")
        # Second query returns tickets from ALL channels
        mock_conn.fetch.return_value = [
            {
                "id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "ticket_number": "TKT-20240312-0001",
                "subject": "Email: Connector setup",
                "status": "open",
                "priority": "medium",
                "channel": "email",
                "category": "technical",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "resolved_at": None,
                "message_count": 3,
            },
            {
                "id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "ticket_number": "TKT-20240312-0002",
                "subject": "WhatsApp: Order status",
                "status": "open",
                "priority": "medium",
                "channel": "whatsapp",
                "category": "general",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "resolved_at": None,
                "message_count": 1,
            },
        ]
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        history = await db.get_customer_history(mock_db_pool, "alice@acmecorp.com", limit=10)

        assert len(history) == 2
        channels = {h["channel"] for h in history}
        assert "email" in channels
        assert "whatsapp" in channels

    async def test_history_empty_for_unknown_identifier(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None  # No identifier found
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        history = await db.get_customer_history(mock_db_pool, "unknown@test.com", limit=10)

        assert history == []


class TestCrossChannelResolution:
    """End-to-end scenario: same person via 2 channels resolves to 1 customer."""

    async def test_phone_then_email_resolves_to_same_customer(self):
        """Simulate: customer first WhatsApps, then emails — both resolve to one customer."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__.return_value = mock_conn
        mock_pool.acquire = MagicMock(return_value=mock_conn)

        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)

        shared_customer_id = UUID("44444444-4444-4444-4444-444444444444")

        # First contact: WhatsApp
        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact match → not found
            # Step 2: identifier_type != "email" → skip fuzzy email
            None,  # Step 3: fuzzy name match ("WhatsApp User") → not found
            {  # Step 4: INSERT customer
                "id": shared_customer_id,
                "email": "+14155551234@phone.techflow.io",
                "name": "WhatsApp User",
                "company": None,
                "tier": "starter",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        whatsapp_customer = await db.get_customer_or_create_by_identifier(
            mock_pool, "phone", "+14155551234", name="WhatsApp User"
        )
        assert whatsapp_customer["id"] == shared_customer_id
        whatsapp_id = whatsapp_customer["id"]

        # Reset mock for second call (new customer_or_create call)
        mock_conn.fetchrow.reset_mock()
        mock_conn.fetchrow.side_effect = [
            {  # SELECT by email - now FOUND (already linked)
                "id": shared_customer_id,
                "email": "alice@acmecorp.com",
                "name": "Alice Johnson",
                "company": "Acme Corp",
                "tier": "enterprise",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        email_customer = await db.get_customer_or_create_by_identifier(
            mock_pool, "email", "alice@acmecorp.com", name="Alice Johnson"
        )

        assert email_customer["id"] == whatsapp_id
        assert email_customer["email"] == "alice@acmecorp.com"

    async def test_webform_phone_linking(self):
        """When webform has both email and phone, link phone to the same customer."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__.return_value = mock_conn
        mock_pool.acquire = MagicMock(return_value=mock_conn)

        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)

        customer_id = UUID("55555555-5555-5555-5555-555555555555")
        mock_conn.execute.return_value = None

        mock_conn.fetchrow.side_effect = [
            None,  # Step 1: exact identifier → not found
            None,  # Step 1b: exact customers.email → not found
            None,  # Step 2: similar email → none
            None,  # Step 3: similar name → none
            {  # Step 4: INSERT customer
                "id": customer_id,
                "email": "bob@startupinc.com",
                "name": "Bob Smith",
                "company": "Startup Inc",
                "tier": "growth",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            },
        ]

        customer = await db.get_customer_or_create_by_identifier(
            mock_pool, "email", "bob@startupinc.com", name="Bob Smith"
        )
        assert customer["id"] == customer_id

        await db.link_identifiers(
            mock_pool,
            customer_id=customer["id"],
            identifiers=[("phone", "+14155559876")],
        )

        # Reset fetchrow for the follow-up lookup (side_effect is exhausted)
        mock_conn.fetchrow.side_effect = None
        mock_conn.fetchrow.return_value = {
            "id": customer_id,
            "email": "bob@startupinc.com",
            "name": "Bob Smith",
            "company": "Startup Inc",
            "tier": "growth",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "metadata": {},
        }

        phone_customer = await db.get_customer_by_identifier(mock_pool, "phone", "+14155559876")
        assert phone_customer is not None
        assert phone_customer["id"] == customer_id


class TestCustomerMerge:
    """Test the human-resolution path for flagged duplicate customers."""

    TARGET = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    SOURCE = UUID("bbbbbbbb-0000-0000-0000-000000000002")

    def _setup_conn_with_transaction(self, mock_conn):
        mock_conn.__aenter__.return_value = mock_conn
        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)

    def _merge_conn(self, mock_db_pool, locked_rows):
        mock_conn = AsyncMock()
        self._setup_conn_with_transaction(mock_conn)
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.return_value = locked_rows
        mock_conn.execute.return_value = "UPDATE 3"
        mock_conn.fetchrow.return_value = {
            "id": self.TARGET,
            "email": "zayn.malik@corpa.com",
            "name": "Zayn Malik",
            "company": None,
            "tier": "starter",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "metadata": {},
        }
        return mock_conn

    async def test_merge_moves_rows_and_deletes_source(self, mock_db_pool):
        mock_conn = self._merge_conn(
            mock_db_pool,
            [
                {"id": self.TARGET, "email": "zayn.malik@corpa.com", "name": "Zayn Malik"},
                {"id": self.SOURCE, "email": "zayn.malick@corpb.com", "name": "Zayn Malick"},
            ],
        )

        result = await db.merge_customers(
            mock_db_pool, self.TARGET, self.SOURCE, reason="same person"
        )

        assert result["source_deleted"] is True
        assert result["source_email"] == "zayn.malick@corpb.com"
        # Every table that references customers must be repointed
        assert set(result["moved"]) == {
            "tickets",
            "messages",
            "agent_runs",
            "customer_identifiers",
        }
        statements = " ".join(str(c.args[0]) for c in mock_conn.execute.await_args_list)
        assert "DELETE FROM customers WHERE id = $1" in statements
        # The source email stays reachable after the merge
        assert "INSERT INTO customer_identifiers" in statements

    async def test_merge_rejects_same_id(self, mock_db_pool):
        with pytest.raises(ValueError, match="must differ"):
            await db.merge_customers(mock_db_pool, self.TARGET, self.TARGET)

    async def test_merge_rejects_missing_source(self, mock_db_pool):
        self._merge_conn(
            mock_db_pool,
            [{"id": self.TARGET, "email": "zayn.malik@corpa.com", "name": "Zayn Malik"}],
        )

        with pytest.raises(ValueError, match="source customer"):
            await db.merge_customers(mock_db_pool, self.TARGET, self.SOURCE)

    async def test_merge_rejects_missing_target(self, mock_db_pool):
        self._merge_conn(
            mock_db_pool,
            [{"id": self.SOURCE, "email": "zayn.malick@corpb.com", "name": "Zayn Malick"}],
        )

        with pytest.raises(ValueError, match="target customer"):
            await db.merge_customers(mock_db_pool, self.TARGET, self.SOURCE)

    async def test_dismiss_review_returns_none_for_unknown_customer(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        assert await db.dismiss_identity_review(mock_db_pool, self.TARGET) is None

    async def test_review_queue_returns_candidates(self, mock_db_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "id": self.SOURCE,
                "email": "zayn.malick@corpb.com",
                "name": "Zayn Malick",
                "candidate_id": self.TARGET,
                "candidate_name": "Zayn Malik",
                "similarity": 0.77,
                "ticket_count": 1,
                "candidate_ticket_count": 4,
            }
        ]
        mock_db_pool.acquire.return_value.__aenter__.return_value = mock_conn

        queue = await db.list_identity_review_queue(mock_db_pool)

        assert len(queue) == 1
        assert queue[0]["candidate_id"] == self.TARGET
