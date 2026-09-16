import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "../../fixtures/test";
import { createFolder, getDriveEntity } from "../../helpers/drive";
import { frappeData } from "../../../shared/frappe";

function uniqueName(runId: string, label: string): string {
	return `${label}-${runId}-${Date.now()}`;
}

async function share(
	request: APIRequestContext,
	entity: string,
	user: string,
	access: Record<string, number>,
): Promise<void> {
	const response = await request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{ form: { entity_name: entity, method: "share", user, ...access } },
	);
	if (!response.ok())
		throw new Error(`share failed: ${await response.text()}`);
}

async function canRead(
	request: APIRequestContext,
	entity: string,
): Promise<boolean> {
	const response = await request.get(
		"/api/method/suite.suite_drive.api.permissions.get_entity_with_permissions",
		{ params: { entity_name: entity } },
	);
	return response.ok();
}

async function makeGroup(
	request: APIRequestContext,
	runId: string,
	name: string,
	members: string[],
): Promise<{ name: string; member_count: number }> {
	const response = await request.post(
		"/api/method/suite.suite_drive.e2e_api.create_user_group",
		{ form: { run_id: runId, name, members: members.join(",") } },
	);
	return frappeData(response);
}

test("a direct share grants read and unsharing takes it away", async ({
	owner,
	collaborator,
	run,
}) => {
	await owner.page.goto("/drive");
	const folder = await createFolder(
		owner.page,
		uniqueName(run.run_id, "share"),
	);

	expect(await canRead(collaborator.page.request, folder.name)).toBe(false);

	await share(owner.page.request, folder.name, collaborator.user.email, {
		read: 1,
	});
	expect(await canRead(collaborator.page.request, folder.name)).toBe(true);

	const shared = await getDriveEntity(collaborator.page.request, folder.name);
	expect(Boolean(shared.write)).toBe(false);

	await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: folder.name,
				method: "unshare",
				user: collaborator.user.email,
			},
		},
	);
	expect(await canRead(collaborator.page.request, folder.name)).toBe(false);
});

test("a share inherits to children until an explicit deny", async ({
	owner,
	collaborator,
	run,
}) => {
	await owner.page.goto("/drive");
	const parent = await createFolder(
		owner.page,
		uniqueName(run.run_id, "inherit"),
	);
	await owner.page.goto(`/drive/d/${parent.name}`);
	const child = await createFolder(
		owner.page,
		uniqueName(run.run_id, "inheritchild"),
		parent.name,
	);

	await share(owner.page.request, parent.name, collaborator.user.email, {
		read: 1,
	});
	// No row on the child — it reads through the parent's grant.
	expect(await canRead(collaborator.page.request, child.name)).toBe(true);

	await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: child.name,
				method: "share",
				user: collaborator.user.email,
				read: 1,
				deny: 1,
			},
		},
	);
	expect(await canRead(collaborator.page.request, child.name)).toBe(false);
	expect(await canRead(collaborator.page.request, parent.name)).toBe(true);
});

test("sharing with a user group reaches its members only", async ({
	owner,
	collaborator,
	run,
}) => {
	await owner.page.goto("/drive");
	const folder = await createFolder(
		owner.page,
		uniqueName(run.run_id, "grp"),
	);
	const group = await makeGroup(owner.page.request, run.run_id, "readers", [
		collaborator.user.email,
	]);
	expect(group.member_count).toBe(1);

	expect(await canRead(collaborator.page.request, folder.name)).toBe(false);

	await share(owner.page.request, folder.name, `$GROUP:${group.name}`, {
		read: 1,
	});
	expect(await canRead(collaborator.page.request, folder.name)).toBe(true);

	// The owner is not in the group, so the row is listed as a group, not a person.
	const response = await owner.page.request.get(
		"/api/method/suite.suite_drive.api.permissions.get_shared_with_list",
		{ params: { entity: folder.name } },
	);
	const rows = await frappeData<Array<Record<string, unknown>>>(response);
	const groupRow = rows.find((r) => r.user === `$GROUP:${group.name}`);
	expect(groupRow?.is_group).toBeTruthy();
});

test("a group deny outranks a group grant on the same folder", async ({
	owner,
	collaborator,
	run,
}) => {
	await owner.page.goto("/drive");
	const folder = await createFolder(
		owner.page,
		uniqueName(run.run_id, "grpdeny"),
	);
	const allowed = await makeGroup(owner.page.request, run.run_id, "allowed", [
		collaborator.user.email,
	]);
	const blocked = await makeGroup(owner.page.request, run.run_id, "blocked", [
		collaborator.user.email,
	]);

	await share(owner.page.request, folder.name, `$GROUP:${allowed.name}`, {
		read: 1,
	});
	expect(await canRead(collaborator.page.request, folder.name)).toBe(true);

	await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: folder.name,
				method: "share",
				user: `$GROUP:${blocked.name}`,
				read: 1,
				deny: 1,
			},
		},
	);
	// Same specificity tier, so the deny wins.
	expect(await canRead(collaborator.page.request, folder.name)).toBe(false);
});

test("you cannot grant access you do not hold yourself", async ({
	owner,
	collaborator,
	run,
}) => {
	await owner.page.goto("/drive");
	const folder = await createFolder(
		owner.page,
		uniqueName(run.run_id, "ceiling"),
	);
	// Collaborator may read and re-share, but not write.
	await share(owner.page.request, folder.name, collaborator.user.email, {
		read: 1,
		share: 1,
	});

	const response = await collaborator.page.request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: folder.name,
				method: "share",
				user: collaborator.user.email,
				read: 1,
				write: 1,
			},
		},
	);
	expect(response.ok()).toBe(false);
	expect(await response.text()).toContain("cannot grant");
});
