import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { frappeData } from "../../shared/frappe";

export interface DriveEntity {
	name: string;
	file_name: string;
	is_folder: boolean | number;
}

export interface DriveEntityPermissions extends DriveEntity {
	read: boolean | number;
	write: boolean | number;
	upload: boolean | number;
	share: boolean | number;
}

export async function driveEntities(
	request: APIRequestContext,
	parent?: string,
): Promise<DriveEntity[]> {
	const response = await request.get("/api/method/suite.suite_drive.api.list.files", {
		params: parent ? { entity_name: parent } : undefined,
	});
	return frappeData<DriveEntity[]>(response);
}

/** Poll until `fileName` is no longer present in the (active) listing of `parent`. */
export async function expectDriveEntityAbsent(
	request: APIRequestContext,
	fileName: string,
	parent?: string,
): Promise<void> {
	await expect
		.poll(async () =>
			(await driveEntities(request, parent)).some(
				(candidate) => candidate.file_name === fileName,
			),
		)
		.toBe(false);
}

export async function waitForDriveEntity(
	request: APIRequestContext,
	fileName: string,
	parent?: string,
): Promise<DriveEntity> {
	let entity: DriveEntity | undefined;
	await expect
		.poll(async () => {
			entity = (await driveEntities(request, parent)).find(
				(candidate) => candidate.file_name === fileName,
			);
			return entity?.name;
		})
		.toBeTruthy();
	return entity as DriveEntity;
}

export async function getDriveEntity(
	request: APIRequestContext,
	entityName: string,
): Promise<DriveEntityPermissions> {
	const response = await request.get(
		"/api/method/suite.suite_drive.api.permissions.get_entity_with_permissions",
		{ params: { entity_name: entityName } },
	);
	return frappeData<DriveEntityPermissions>(response);
}

export async function createFolder(
	page: Page,
	name: string,
	parent?: string,
): Promise<DriveEntity> {
	await page.getByRole("button", { name: "Create", exact: true }).click();
	await page.getByRole("menuitem", { name: "Folder", exact: true }).click();
	const dialog = page.getByRole("dialog", { name: "Create a folder" });
	await dialog.getByRole("textbox", { name: "Name:" }).fill(name);
	await dialog.getByRole("button", { name: "Create", exact: true }).click();
	return waitForDriveEntity(page.request, name, parent);
}

export async function shareCurrentEntity(
	page: Page,
	entityTitle: string,
	user: string,
): Promise<void> {
	await page.getByRole("button", { name: "Entity actions" }).click();
	await page.getByRole("menuitem", { name: "Share" }).click();
	const dialog = page.getByRole("dialog", { name: new RegExp(entityTitle) });
	const peopleInput = dialog.getByPlaceholder("Add people");
	await peopleInput.fill(user);
	const userOption = page.getByRole("option", { name: `Add "${user}"` });
	await expect(userOption).toBeVisible();
	await peopleInput.press("Enter");
	await expect(dialog.getByRole("button", { name: "Invite" })).toBeVisible();
	await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().includes("suite.suite_drive.api.files.update_access") &&
				response.ok(),
		),
		dialog.getByRole("button", { name: "Invite" }).click(),
	]);
}

export async function setDriveAccess(
	request: APIRequestContext,
	entityName: string,
	user: string,
): Promise<void> {
	const response = await request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: entityName,
				method: "share",
				user,
				read: 1,
				comment: 1,
				share: 0,
				write: 0,
				upload: 0,
			},
		},
	);
	if (!response.ok()) {
		throw new Error(`Updating Drive access failed: ${await response.text()}`);
	}
}

export async function openEntityActions(page: Page, entityName: string) {
	const entity = page.getByTestId(`drive-entity-${entityName}`);
	await expect(entity).toBeVisible();
	await entity.click({ button: "right" });
}
