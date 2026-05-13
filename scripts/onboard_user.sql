\set ON_ERROR_STOP on

BEGIN;

-- Ensure target OpenClaw instance exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM instance
        WHERE instance_uuid = :'instance_uuid'
    ) THEN
        RAISE EXCEPTION 'Instance "%" does not exist in table "instance"', :'instance_uuid';
    END IF;
END
$$;

-- Upsert canonical user.
INSERT INTO app_user (id, external_user_id, role)
VALUES (:'app_user_id', :'external_user_id', NULLIF(:'role', ''))
ON CONFLICT (id) DO UPDATE
SET
    external_user_id = EXCLUDED.external_user_id,
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
