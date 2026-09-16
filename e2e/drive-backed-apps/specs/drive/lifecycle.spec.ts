import { expect, test } from "../../fixtures/test";
import {
	createFolder,
	expectDriveEntityAbsent,
	waitForDriveEntity,
} from "../../helpers/drive";

function uniqueName(runId: string, label: string): string {
	return `${label}-${runId}-${Date.now()}`;
}

test("permanently deleting a trashed entity removes it irrecoverably", async ({
	owner,
	run,
}) => {
	await owner.page.goto("/drive");
	const folder = await createFolder(owner.page, uniqueName(run.run_id, "perm-del"));

	// Trash, then hard-delete.
	const trash = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.remove_or_restore",
		{ data: { entity_names: [folder.name] } },
	);
	expect(trash.ok()).toBe(true);
	const purge = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.delete_entities",
		{ data: { entity_names: [folder.name] } },
	);
	expect(purge.ok()).toBe(true);

	// The entity is gone — even its owner can no longer resolve it.
	const lookup = await owner.page.request.get(
		"/api/method/suite.suite_drive.api.permissions.get_entity_with_permissions",
		{ params: { entity_name: folder.name } },
	);
	expect(lookup.ok()).toBe(false);
});

test("trashing a folder hides it from Home; restore brings the folder and its child back", async ({
	owner,
	run,
}) => {
	await owner.page.goto("/drive");
	const parentName = uniqueName(run.run_id, "cascade-parent");
	const parent = await createFolder(owner.page, parentName);

	await owner.page.goto(`/drive/d/${parent.name}`);
	const childName = uniqueName(run.run_id, "cascade-child");
	await createFolder(owner.page, childName, parent.name);
	await waitForDriveEntity(owner.page.request, childName, parent.name);

	// Trash the parent → it disappears from the Home listing.
	const trash = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.remove_or_restore",
		{ data: { entity_names: [parent.name] } },
	);
	expect(trash.ok()).toBe(true);
	await expectDriveEntityAbsent(owner.page.request, parentName);

	// Restore the parent → it returns to Home with its child intact inside it.
	const restore = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.remove_or_restore",
		{ data: { entity_names: [parent.name] } },
	);
	expect(restore.ok()).toBe(true);
	await waitForDriveEntity(owner.page.request, parentName);
	await waitForDriveEntity(owner.page.request, childName, parent.name);
});
