\set ON_ERROR_STOP on

BEGIN;

-- Abort early unless target OpenClaw instance exists.
SELECT instance_uuid AS checked_instance_uuid
FROM instance
WHERE instance_uuid = :'instance_uuid'
\gset

-- Upsert canonical user only after the instance check succeeds.
INSERT INTO app_user (id, external_user_id, role)
SELECT :'app_user_id', :'external_user_id', NULLIF(:'role', '')
WHERE :'checked_instance_uuid' = :'instance_uuid'
ON CONFLICT (id) DO UPDATE
SET external_user_id = EXCLUDED.external_user_id,
    role = COALESCE(EXCLUDED.role, app_user.role);

-- Keep one identity per provider for this user.
DELETE FROM user_identity
WHERE user_id = :'app_user_id'
  AND provider = :'provider';

-- Upsert provider identity.
INSERT INTO user_identity (user_id, provider, provider_user_id)
VALUES (:'app_user_id', :'provider', :'provider_user_id')
ON CONFLICT (provider, provider_user_id) DO UPDATE
SET user_id = EXCLUDED.user_id;

-- Reassign both user and instance to keep 1:1 mapping.
DELETE FROM user_instance
WHERE user_id = :'app_user_id'
   OR instance_uuid = :'instance_uuid';

INSERT INTO user_instance (instance_uuid, user_id)
VALUES (:'instance_uuid', :'app_user_id');

COMMIT;
