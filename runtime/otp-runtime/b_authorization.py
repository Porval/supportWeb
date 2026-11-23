"""Administrator authorization shared by identity providers (JT-024 R4).

Only server-owned system_permissions identify administrators. Business roles,
client capability fields, usernames, phone aliases and hostnames do not.
Identity providers bind this capability after authenticating on every request.
"""

ADMIN_ACTIONS = frozenset(('operations.read','operations.download','submissions.create','submissions.read',
                           'submissions.read_own','submissions.edit_own','activities.propose','materials.read',
                           'materials.download','reviews.read','reviews.decide','platform_materials.write','activities.rate'))


class AdministratorAuthorization:
    def full_access_enabled(self, db, actor):
        return bool(actor['status']=='active' and db.execute(
            "SELECT 1 FROM system_permissions WHERE user_id=? AND permission='accounts.manage'",
            (actor['id'],)).fetchone())

    def bind_authorization(self, db, actor):
        # Always replace any old value; no role or cached session claim grants admin.
        return dict(actor) | {'_administrator':self.full_access_enabled(db,actor)}

    def has_full_access(self, actor):
        return isinstance(actor,dict) and actor.get('status')=='active' and actor.get('_administrator') is True

    def is_review_admin(self, actor):
        return self.has_full_access(actor)

    def scopes(self, db, actor, action):
        if action in ADMIN_ACTIONS and self.full_access_enabled(db,actor):
            return {'*'}
        return {row[0] for row in db.execute('SELECT resource_id FROM explicit_grants WHERE user_id=? AND action=?',
                                           (actor['id'],action))}
