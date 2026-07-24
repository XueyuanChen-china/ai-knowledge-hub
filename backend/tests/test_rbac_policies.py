import unittest

from app.security.policies import (
    PERMISSION_CHAT,
    PERMISSION_CONTENT_DELETE,
    PERMISSION_CONTENT_READ,
    PERMISSION_CONTENT_WRITE,
    PERMISSION_KNOWLEDGE_BASE_DELETE,
    PERMISSION_KNOWLEDGE_BASE_READ,
    PERMISSION_KNOWLEDGE_BASE_WRITE,
    PERMISSION_SEARCH,
    PERMISSION_UPLOAD,
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_OWNER,
    ROLE_VIEWER,
    has_permission,
)


class RbacPolicyTests(unittest.TestCase):
    def test_viewer_can_read_and_use_but_cannot_write(self) -> None:
        self.assertTrue(has_permission(ROLE_VIEWER, PERMISSION_KNOWLEDGE_BASE_READ))
        self.assertTrue(has_permission(ROLE_VIEWER, PERMISSION_CONTENT_READ))
        self.assertTrue(has_permission(ROLE_VIEWER, PERMISSION_SEARCH))
        self.assertTrue(has_permission(ROLE_VIEWER, PERMISSION_CHAT))
        self.assertFalse(has_permission(ROLE_VIEWER, PERMISSION_KNOWLEDGE_BASE_WRITE))
        self.assertFalse(has_permission(ROLE_VIEWER, PERMISSION_CONTENT_WRITE))
        self.assertFalse(has_permission(ROLE_VIEWER, PERMISSION_UPLOAD))
        self.assertFalse(has_permission(ROLE_VIEWER, PERMISSION_CONTENT_DELETE))

    def test_editor_can_write_content_but_not_delete_knowledge_base(self) -> None:
        self.assertTrue(has_permission(ROLE_EDITOR, PERMISSION_CONTENT_WRITE))
        self.assertTrue(has_permission(ROLE_EDITOR, PERMISSION_KNOWLEDGE_BASE_WRITE))
        self.assertFalse(has_permission(ROLE_EDITOR, PERMISSION_KNOWLEDGE_BASE_DELETE))

    def test_admin_and_owner_can_manage_resources(self) -> None:
        for role in (ROLE_ADMIN, ROLE_OWNER):
            self.assertTrue(has_permission(role, PERMISSION_KNOWLEDGE_BASE_DELETE))
            self.assertTrue(has_permission(role, PERMISSION_CONTENT_DELETE))

    def test_unknown_role_has_no_permissions(self) -> None:
        self.assertFalse(has_permission("unknown", PERMISSION_CONTENT_READ))


if __name__ == "__main__":
    unittest.main()
