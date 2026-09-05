-- 008: Guard fuzzy email identity matching with a local-part edit-distance check.
--
-- Trigram similarity alone merged unrelated customers: e.g.
--   similarity('dup-test-1787409712@example.com', 'test@example.com') = 0.53
-- which is well above the 0.3 threshold, so a brand-new customer's ticket was
-- attached to an existing, different customer.
--
-- levenshtein() on the local part separates real typos (jon/john = 1) from
-- different people (dup-test-1787409712/test = 15).
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
