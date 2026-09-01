-- disable ecartpay payment provider
UPDATE payment_provider
   SET ecartpay_public_key = NULL,
       ecartpay_private_key = NULL,
       ecartpay_webhook_secret = NULL,
       ecartpay_auth_token = NULL,
       ecartpay_token_expiry = NULL;
