"""Shared constants, sample payloads and response builders for the vBackup tests.

Fixtures live in ``conftest.py``; plain values and helpers live here so test
modules can import them directly (importing from a conftest is discouraged and
breaks once the test package is collected under a different rootdir).

The sample payloads mirror the shapes the live gateway returns — float policy
numbers, byte sizes, JSON-string snapshots — so a model that only works against
tidied-up fixtures fails here.
"""

from __future__ import annotations

import httpx
import json
import respx
from greennode.vbackup_mcp_server.config import (
    REGIONS,
    VBACKUP_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_RELATIONAL_SERVICE,
)


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API_BASE = REGIONS["HCM-3"][VBACKUP_SERVICE]
HAN_BASE = REGIONS["HAN"][VBACKUP_SERVICE]

GIB = 1024**3


def mock_iam(mock: respx.MockRouter) -> None:
    """Route the IAM token endpoint to a canned token."""
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def envelope(items: list[dict], total: int | None = None) -> dict:
    """The list envelope every vBackup collection answers with.

    Note the PLURAL totalItems/totalPages — vServer's collections spell the
    same fields singular, and a helper copied across reads the count as absent.
    """
    return {
        "items": items,
        "page": None,
        "pageSize": None,
        "totalPages": 1,
        "totalItems": total if total is not None else len(items),
    }


POLICY_CONFIG = {
    "hour": 12.0,
    "minute": 0.0,
    "timeZone": "Asia/Ho_Chi_Minh",
    "hourlyEnabled": True,
    "hourlyConfig": {
        "interval": 4.0,
        "retention": 1.0,
        "backupType": "INCREMENTAL",
        "incrementalQuantity": 3.0,
    },
    "dailyEnabled": True,
    "dailyConfig": {"retention": 3.0, "backupType": "FULL", "incrementalQuantity": 0.0},
    "weeklyEnabled": False,
    "weeklyConfig": {},
    "monthlyEnabled": False,
    "monthlyConfig": {},
    "isProtectedServer": True,
    "statusSendEmail": ["ERROR"],
}

RAW_POLICY = {
    "id": "bk-pol-0001",
    "userId": 1,
    "backendId": "be-0001",
    "projectId": "pro-0001",
    "name": "nightly",
    "isDefault": False,
    "product": "vServer",
    "backupInstanceCount": 2,
    "config": POLICY_CONFIG,
    "createdAt": "2026-05-25T02:40:27.000+00:00",
    "updatedAt": "2026-08-03T12:11:27.000+00:00",
}

RAW_DESTINATION = {
    "id": "bk-des-0001",
    "userId": 1,
    "backendId": None,
    "projectId": None,
    "name": "default-vault",
    "status": "ACTIVE",
    "isDefault": True,
    "type": "VAULT",
    "product": "vServer",
    "numberOfBackupInstances": 4,
    "maxQuota": {"unlimited": False, "maxQuota": 150},
    "softDeleteConfig": {
        "enable": True,
        "retainDays": 8,
        "createdAt": "2026-05-25T02:40:27.000+00:00",
    },
    "vaultLock": None,
    "config": {
        "vault": {
            "regionId": "rgn-0001",
            "regionName": "HCM04",
            "used": 30 * GIB,
            "total": 0,
            "traffic": 0,
        },
        "vstorage": None,
    },
    "createdAt": "2026-05-25T02:40:27.000+00:00",
    "updatedAt": "2026-08-03T12:11:27.000+00:00",
}
"""A VAULT destination with a real quota object and soft delete on.

``maxQuota`` is an OBJECT, not a byte count, and the number in it is GB — the
API's own history text spells it ``{max-quota: 150GB}``. A model that reads it
as a scalar reports every quota as 0.
"""

RAW_DESTINATION_VSTORAGE = {
    **RAW_DESTINATION,
    "id": "bk-des-0002",
    "name": "legacy-store",
    "type": "VSTORAGE",
    "isDefault": False,
    "maxQuota": {"unlimited": True, "maxQuota": 0},
    "softDeleteConfig": None,
    "config": {
        "vault": None,
        "vstorage": {
            "regionId": "rgn-0002",
            "regionName": "HCM03",
            "projectName": "Default-vbackup-project",
            "containerName": "container-0002",
            "storageService": "vstorage",
            "used": 10 * GIB,
            "total": 30 * GIB,
        },
    },
}
"""A VSTORAGE destination: the same object with `config.vault` null.

Reading only `config.vault` reports this one as an empty, unused store.
"""

RAW_PRODUCTS = [
    {"id": "prd-0001", "product": "vServer", "enabled": True},
    {"id": "prd-0002", "product": "vDB", "enabled": True},
]

RAW_BACKUP_REGIONS = [
    {"id": "vst-cfg-0001", "name": "HCM04", "regionId": "rgn-0001", "product": "vServer"},
    {"id": "vst-cfg-0002", "name": "HAN02", "regionId": "rgn-0002", "product": "vServer"},
]
"""Note `id` and `regionId` differ: a create takes `regionId`."""

RAW_DESTINATION_TAG = {
    "key": "vng.createdBy",
    "value": "Root:1",
    "resourceId": "bk-des-0001",
    "resourceType": "BACKUP_LOCATION",
    "systemTag": True,
}

RAW_DESTINATION_HISTORY = [
    {
        "id": "bk-des-his-0001",
        "backupDestinationId": "bk-des-0001",
        "backupDestinationName": "default-vault",
        "action": "EDIT_MAX_QUOTA",
        "status": "SUCCESS",
        "errorMessage": None,
        "description": "Edit max-quota with {max-quota: 150GB}",
        "createdAt": "2026-08-01T06:43:54.000+00:00",
    },
    {
        "id": "bk-des-his-0002",
        "backupDestinationId": "bk-des-0001",
        "backupDestinationName": "default-vault",
        "action": "DELETE",
        "status": "ERROR",
        "errorMessage": "backup_location_is_being_used",
        "description": "Delete permanently",
        "createdAt": "2026-07-15T03:48:07.000+00:00",
    },
]

RAW_SERVER = {
    "id": "bk-ins-0001",
    "name": "web-01-backup",
    "serverId": "ins-0001",
    "serverDeleted": False,
    "status": "ACTIVE",
    "backupEnabled": True,
    "description": "Created by vServer.",
    "backendId": "be-0001",
    "projectId": "pro-0001",
    "latestRecord": "2026-05-28T05:00:06.000+00:00",
    "createdAt": "2026-05-25T02:40:27.000+00:00",
    "updatedAt": "2026-08-03T12:11:27.000+00:00",
    "volumes": [
        {
            "volumeId": "vol-0001",
            "volumeSize": 20 * GIB,
            "volumeUsage": 5 * GIB,
            "backupEnabled": True,
            "latestRecord": "2026-05-28T05:00:07.000+00:00",
        },
        {
            "volumeId": "vol-0002",
            "volumeSize": 50 * GIB,
            "volumeUsage": 0,
            "backupEnabled": False,
            "latestRecord": None,
        },
    ],
    "policy": {
        "id": "bk-pol-0001",
        "name": "nightly",
        "isDefault": False,
        "config": POLICY_CONFIG,
    },
    "destination": {
        "id": "bk-des-0001",
        "name": "default-vault",
        "status": "ACTIVE",
        "isDefault": True,
        "type": "VAULT",
    },
    "backupPolicyId": "bk-pol-0001",
    "backupDestinationId": "bk-des-0001",
}

RAW_POINT = {
    "id": "bk-ins-pt-0001",
    "userId": 1,
    "backendId": "be-0001",
    "projectId": "pro-0001",
    "backupInstanceId": "bk-ins-0001",
    "serverId": "ins-0001",
    "status": "ACTIVE",
    "snapshotTime": "2026-05-28T05:00:00.000+00:00",
    "finishTime": "2026-05-28T05:03:00.000+00:00",
    "size": 20 * GIB,
    "usage": 5 * GIB,
    "createdAt": "2026-05-28T05:00:00.000+00:00",
    "destination": {"id": "bk-des-0001", "name": "default-vault"},
    "policySnapshot": json.dumps({"id": "bk-pol-0001", "name": "nightly-as-it-was"}),
    "backupVolumePoints": [
        {
            "backupVolumePointId": "bk-vol-pt-0001",
            "name": "web-01 boot_volume",
            "size": 20 * GIB,
            "bootIndex": 0,
            "volumeTypeId": "vtype-0001",
            "bootable": True,
            "multiAttach": False,
            "encryptionType": None,
            "backupInstancePointId": "bk-ins-pt-0001",
        }
    ],
}

RAW_HISTORY = {
    "id": "bk-ins-pt-0001",
    "userId": 1,
    "backendId": "be-0001",
    "projectId": "pro-0001",
    "backupInstanceId": "bk-ins-0001",
    "backupInstanceName": "web-01-backup",
    "serverId": "ins-0001",
    "status": "SUCCESS",
    "deletionStatus": None,
    "errorMessage": None,
    "snapshotTime": "2026-05-28T05:00:00.000+00:00",
    "finishTime": "2026-05-28T05:03:00.000+00:00",
    "size": 20 * GIB,
    "usage": 5 * GIB,
    "policyId": "bk-pol-0001",
    "destinationId": "bk-des-0001",
    "policySnapshot": json.dumps({"id": "bk-pol-0001", "name": "nightly-as-it-was"}),
    "destinationSnapshot": json.dumps({"id": "bk-des-0001", "name": "vault-as-it-was"}),
    "createdAt": "2026-05-28T05:00:00.000+00:00",
}

RAW_STATISTIC = {
    "totalBackupServers": 39,
    "totalProtectedServers": 4,
    "totalServers": 30,
    "totalBackupCompleted": 254,
    "totalBackupFailed": 2,
    "totalRestoreCompleted": 1,
    "totalRestoreFailed": 0,
}

RAW_INSTANCE = {
    "data": {
        "uuid": "ins-0001",
        "name": "web-01",
        "status": "ACTIVE",
        "zoneId": "HCM03-1A",
        "bootVolumeId": "vol-0001",
        "encryptionVolume": False,
        "createdAt": "2026-08-11T14:38:50.000+07:00",
        "flavor": {"name": "s2-general-1x2", "cpu": 1, "memory": 2, "gpu": 0},
        "image": {
            "id": "img-0001",
            "imageType": "Ubuntu",
            "imageVersion": "1_Ubuntu-22.04x64-UEFI",
        },
        "internalInterfaces": [
            {"fixedIp": "192.0.2.10", "floatingIp": None, "interfaceType": "INTERNAL"}
        ],
        "externalInterfaces": [],
    }
}
"""A vServer instance detail, wrapped in the `data` envelope that gateway uses."""

RAW_PRESIGNED = {
    "id": "bk-ins-pt-0001",
    "backupInstanceId": "bk-ins-0001",
    "backupVolumePointPreSignedUrls": [
        {
            "id": "bk-vol-pt-0001",
            "volumeId": "vol-0001",
            "preSignedUrl": [
                "https://example.invalid/part-1?sig=aaa",
                "https://example.invalid/part-2?sig=bbb",
            ],
        }
    ],
}
"""A large disk is split across several signed links; all of them are needed."""

RAW_DB_HISTORY = {
    "id": "bk-db-pt-0001",
    "backupDatabaseId": "bk-db-0001",
    "backupDatabaseName": "orders-db-backup",
    "databaseId": "pg-0001",
    "status": "SUCCESS",
    "errorMessage": None,
    "compressedSize": 2 * GIB,
    "uncompressedSize": 10 * GIB,
    "deletionStatus": "",
    "destinationId": "bk-des-0001",
    "policySnapshot": json.dumps({"id": "bk-pol-0001", "name": "db-nightly-as-it-was"}),
    "destinationSnapshot": json.dumps({"id": "bk-des-0001", "name": "vault-as-it-was"}),
    "createdAt": "2026-06-15T02:00:01.000+00:00",
}
"""A vDB backup run: compressed/uncompressed sizes, not size/usage."""

RAW_DB_RESTORE = {
    "id": "db-res-0001",
    "destinationDatabaseId": "pg-0002",
    "backupDatabaseId": "bk-db-0001",
    "backupDatabaseName": "orders-db-backup",
    "backupDatabasePointId": "bk-db-pt-0001",
    "status": "SUCCESS",
    "finishAt": None,
    "createdAt": "2026-04-02T08:34:25.000+00:00",
    "updatedAt": "2026-04-02T08:44:07.000+00:00",
}

RAW_CONFIGURATION = {
    "configs": {
        "backup_policy_hourly_interval": [4, 6, 8, 12],
        "backup_policy_retention_limit": {
            "hourly": 30000,
            "daily": 30000,
            "weekly": 30000,
            "monthly": 30000,
        },
        "snapshot_policy_hourly_interval": [1, 2, 4, 6, 8, 12],
        "snapshot_policy_retention_limit": {
            "hourly": 64,
            "daily": 64,
            "weekly": 64,
            "monthly": 64,
        },
        "allowed_backup_server_status": "ACTIVE,STOPPED",
        "backup_policy_time_ranges": [
            {"key": 0, "value": "00:00", "enable": False, "description": ""},
            {"key": 1, "value": "1:00", "enable": True, "description": ""},
            {"key": 12, "value": "12:00", "enable": False, "description": ""},
            {"key": 13, "value": "13:00", "enable": True, "description": ""},
        ],
    }
}


VDB_BASE = REGIONS["HCM-3"][VDB_MEMORY_SERVICE]
VDB_RELATIONAL_BASE = REGIONS["HCM-3"][VDB_RELATIONAL_SERVICE]

BACKUP_DB_ID = "bk-db-0001"
DATABASE_ID = "rd-0001"

RAW_BACKUP_DATABASE = {
    "id": BACKUP_DB_ID,
    "userId": 1,
    "name": "database-redis-0001-backup",
    "engine": "Redis",
    "engineVersion": "v7.2.13",
    "databaseId": DATABASE_ID,
    "description": "Created by vDB.",
    "status": "ACTIVE",
    "backupEnabled": True,
    "latestRecord": "2026-08-18T08:30:27.000+00:00",
    "nextSchedule": "2026-08-18T10:00:00.000+00:00",
    "createdAt": "2026-08-18T06:40:47.000+00:00",
    "updatedAt": "2026-08-18T08:30:43.000+00:00",
    "policy": {
        "id": "bk-pol-0001",
        "name": "vdb-policy",
        "isDefault": False,
        "config": POLICY_CONFIG,
    },
    "backupDestination": {
        "id": "bk-des-0001",
        "name": "vdb-location",
        "status": "ACTIVE",
        "isDefault": True,
        "type": "VAULT",
        "config": {"vault": {"regionName": "HCM04", "used": 844.0}},
        "product": "vDB",
    },
    "backupPolicyId": "bk-pol-0001",
    "backupDestinationId": "bk-des-0001",
    "totalBackupSize": 2 * GIB,
    "freeUsage": 50,
    "databaseDeleted": False,
}
"""One backup database, mirroring the live payload — nested policy/destination,
byte-valued totalBackupSize and a GB-valued freeUsage side by side."""

RAW_BACKUP_DATABASE_POINT = {
    "id": "bk-db-pt-0001",
    "userId": 1,
    "backupDatabaseId": BACKUP_DB_ID,
    "databaseId": DATABASE_ID,
    "backupName": "1787041652",
    "status": "ACTIVE",
    "errorMessage": None,
    "compressedSize": 1 * GIB,
    "uncompressedSize": 3 * GIB,
    "createdAt": "2026-08-18T08:30:27.000+00:00",
    "updatedAt": "2026-08-18T08:30:43.000+00:00",
    "policySnapshot": json.dumps(
        {"modes": ["manual"], "backupType": "MANUAL_FULL", "detailModes": []}
    ),
    "time": "2026-08-18T08:27:44Z",
    "destination": {"id": "bk-des-0001", "name": "vdb-location", "type": "VAULT"},
    "isRestoring": None,
}
"""One restore point of a database: two sizes, a JSON-string policy snapshot,
and the numeric `backupName` that is an identifier rather than a timestamp."""


def vdb_envelope(rows: list[dict], project_id: str = "pro-0001") -> dict:
    """The vDB gateway's own envelope — doubly nested, unlike vBackup's."""
    return {
        "code": 200,
        "message": "success",
        "data": {
            "data": rows,
            "pageObject": {"totalPages": 1, "totalElements": len(rows), "size": 1000, "number": 1},
            "projectId": project_id,
        },
    }


RAW_VDB_REDIS = {
    "id": DATABASE_ID,
    "name": "database-redis-0001",
    "status": "ACTIVE",
    "datastoreType": "Redis",
    "datastoreVersion": "v7.2.13",
    "deployType": "non-sharding",
    "numberOfNodes": 3,
    "zoneId": "HCM03-1B",
    "projectId": "pro-0001",
    "vcpus": 2,
    "ram": 4,
    "volumeSize": 0,
    "backupAuto": False,
    "created": "2026-08-18 13:40:45.874",
}

RAW_VDB_POSTGRES_CLUSTER = {
    "id": "pg-0001",
    "name": "database-pg-0001",
    "status": "ACTIVE",
    "datastoreType": "PostgreSQL",
    "datastoreVersion": "17",
    "deployType": "cluster",
    "numberOfNodes": 3,
    "zoneId": "HCM03-1B",
    "projectId": "pro-0001",
    "vcpus": 2,
    "ram": 4,
    "volumeSize": 20,
    "backupAuto": False,
    "created": "2026-08-01 10:00:00.000",
}

RAW_VDB_POSTGRES_SINGLE = {
    **RAW_VDB_POSTGRES_CLUSTER,
    "id": "pg-0002",
    "name": "database-pg-0002",
    "deployType": "single",
}
"""A single-node PostgreSQL — the one topology vBackup cannot protect."""
