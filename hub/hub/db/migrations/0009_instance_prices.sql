-- What a domain costs, in a form something can render.
--
-- Migration 0006 stored the Stripe PRICE IDS, which is what Checkout needs and
-- all it needs. But an id carries no amount, so nothing on this side could say
-- "$12 a month" until somebody had already subscribed -- `subscriptions.
-- unit_amount` only exists once there is a subscription to put it on. The
-- gallery a stranger chooses a domain from has to name the price BEFORE that.
--
-- Written by `hub instances pricing` out of the Price object it already
-- receives, so this is a second read of one fact rather than a second source
-- for it. It can still part from Stripe if somebody edits a price in the
-- dashboard, so `poller.refresh_prices` re-reads each one hourly and corrects
-- the quote -- the visible half being the one a stranger reads before deciding
-- whether to pay it (ADR 0215).
ALTER TABLE instances ADD COLUMN price_monthly_amount INTEGER;
ALTER TABLE instances ADD COLUMN price_annual_amount  INTEGER;

-- Beside the amounts rather than derived from them: Stripe's minor unit is not
-- always 1/100 (JPY has none), so a renderer that assumed cents would be wrong
-- in the one currency where it matters most to be right.
ALTER TABLE instances ADD COLUMN price_currency TEXT;
