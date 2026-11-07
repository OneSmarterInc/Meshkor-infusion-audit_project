import test from "node:test";
import assert from "node:assert/strict";
import { canAccess, hasRole, isClientOnly, workspaceRole } from "./rolePolicy.js";

test("legacy aliases retain only their corresponding application role", () => {
  assert.equal(canAccess({ roles: ["Admin"] }, "admin"), true);
  assert.equal(canAccess({ roles: ["Viewer"] }, "client"), true);
  assert.equal(canAccess({ roles: ["Viewer"] }, "analyst"), false);
  assert.equal(canAccess({ roles: ["Admin"] }, "claims"), true);
});

test("multiple roles combine capabilities and dashboard selection uses least-surprise precedence", () => {
  const user = { roles: ["CLIENT", "REVIEWER"] };
  assert.equal(canAccess(user, "planAudits"), true);
  assert.equal(canAccess(user, "claims"), false);
  assert.equal(workspaceRole(user), "REVIEWER");
  assert.equal(isClientOnly(user), false);
});

test("unknown or missing roles fail closed", () => {
  assert.equal(hasRole({ roles: ["CUSTOM"] }, "SUPER_ADMIN", "ANALYST"), false);
  assert.equal(canAccess({ roles: [] }, "admin"), false);
  assert.equal(workspaceRole({}), null);
});

test("client and reviewer policies keep sensitive screens separate", () => {
  assert.equal(canAccess({ roles: ["CLIENT"] }, "claims"), false);
  assert.equal(canAccess({ roles: ["CLIENT"] }, "admin"), false);
  assert.equal(canAccess({ roles: ["REVIEWER"] }, "reviewer"), true);
  assert.equal(canAccess({ roles: ["REVIEWER"] }, "analyst"), false);
  assert.equal(isClientOnly({ roles: ["Viewer"] }), true);
});
