import type { Page } from "@playwright/test";

import { expect, test } from "../../fixtures/test";
import { createFolder } from "../../helpers/drive";

function uniqueName(runId: string, label: string): string {
	return `${label}-${runId}-${Date.now()}`;
}

/** The breadcrumb trail a user can read in the navbar, outermost first. */
async function trail(page: Page): Promise<string[]> {
	const crumbs = page.getByTestId("breadcrumbs");
	await expect(crumbs).toBeVisible();
	await expect(page.getByTestId("breadcrumbs-loading")).toHaveCount(0);
	return (await crumbs.innerText())
		.split("/")
		.map((part: string) => part.trim())
		.filter(Boolean);
}

async function openFolder(page: Page, entityName: string): Promise<void> {
	const row = page.getByTestId(`drive-entity-${entityName}`);
	await expect(row).toBeVisible();
	await row.click();
}

test("each section shows its own name, and the navbar never vanishes while navigating", async ({
	owner,
}) => {
	const page = owner.page;
	await page.goto("/drive");
	await expect.poll(() => trail(page)).toEqual(["Home"]);

	for (const section of ["Recents", "Favourites", "Trash"]) {
		await page.getByRole("link", { name: section, exact: true }).click();
		await expect(page.locator("#navbar")).toBeVisible();
		await expect.poll(() => trail(page)).toEqual([section]);
	}

	await page.getByRole("link", { name: "Home", exact: true }).click();
	await expect.poll(() => trail(page)).toEqual(["Home"]);
});

test("nested folders build up the trail and an ancestor crumb navigates back", async ({
	owner,
	run,
}) => {
	const page = owner.page;
	await page.goto("/drive");
	const parentName = uniqueName(run.run_id, "parent");
	const parent = await createFolder(page, parentName);

	await page.goto(`/drive/d/${parent.name}`);
	await expect.poll(() => trail(page)).toEqual(["Home", parentName]);

	const childName = uniqueName(run.run_id, "child");
	const child = await createFolder(page, childName, parent.name);
	await page.reload();
	await openFolder(page, child.name);
	await expect(page).toHaveURL(new RegExp(child.name));
	await expect.poll(() => trail(page)).toEqual(["Home", parentName, childName]);

	await page.locator("#navbar").getByRole("link", { name: parentName }).click();
	await expect(page).toHaveURL(new RegExp(parent.name));
	await expect.poll(() => trail(page)).toEqual(["Home", parentName]);
});

test("a deep link into a nested folder shows the full trail", async ({
	owner,
	run,
}) => {
	const page = owner.page;
	await page.goto("/drive");
	const parentName = uniqueName(run.run_id, "deep");
	const parent = await createFolder(page, parentName);
	await page.goto(`/drive/d/${parent.name}`);
	const childName = uniqueName(run.run_id, "deepchild");
	const child = await createFolder(page, childName, parent.name);

	await page.goto(`/drive/d/${child.name}`);
	await expect.poll(() => trail(page)).toEqual(["Home", parentName, childName]);

	await page.reload();
	await expect.poll(() => trail(page)).toEqual(["Home", parentName, childName]);
});

test("browser back restores the previous trail", async ({ owner, run }) => {
	const page = owner.page;
	await page.goto("/drive");
	const parentName = uniqueName(run.run_id, "back");
	const parent = await createFolder(page, parentName);
	await page.goto(`/drive/d/${parent.name}`);
	const childName = uniqueName(run.run_id, "backchild");
	const child = await createFolder(page, childName, parent.name);
	await page.reload();
	await openFolder(page, child.name);
	await expect.poll(() => trail(page)).toEqual(["Home", parentName, childName]);

	await page.goBack();
	await expect.poll(() => trail(page)).toEqual(["Home", parentName]);

	await page.goBack();
	await expect.poll(() => trail(page)).toEqual(["Home"]);
});

test("renaming the current folder updates its crumb", async ({ owner, run }) => {
	const page = owner.page;
	await page.goto("/drive");
	const original = uniqueName(run.run_id, "renamecrumb");
	const folder = await createFolder(page, original);

	await page.goto(`/drive/d/${folder.name}`);
	await expect.poll(() => trail(page)).toEqual(["Home", original]);

	const renamed = `${original}-renamed`;
	const crumb = page.getByTestId("breadcrumbs").getByText(original, { exact: true });
	const input = page.locator("#navbar").getByRole("textbox");
	await expect(async () => {
		await crumb.click();
		await expect(input).toBeVisible({ timeout: 2000 });
	}).toPass({ timeout: 20000 });
	await input.fill(renamed);
	await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().includes("suite.suite_drive.api.files.rename") &&
				response.ok(),
		),
		input.press("Enter"),
	]);
	await expect.poll(() => trail(page)).toEqual(["Home", renamed]);

	await page.reload();
	await expect.poll(() => trail(page)).toEqual(["Home", renamed]);
});

test("attachments pages show the doctype and document they belong to", async ({
	owner,
}) => {
	const page = owner.page;
	await page.goto("/drive/attachments");
	await expect.poll(() => trail(page)).toEqual(["Attachments"]);

	await page.goto("/drive/attachments/User");
	await expect.poll(() => trail(page)).toEqual(["Attachments", "User"]);
});

test("moving the current folder updates its trail without a reload", async ({
	owner,
	run,
}) => {
	const page = owner.page;
	await page.goto("/drive");
	const destinationName = uniqueName(run.run_id, "dest");
	const destination = await createFolder(page, destinationName);
	const movedName = uniqueName(run.run_id, "moved");
	const moved = await createFolder(page, movedName);

	await page.goto(`/drive/d/${moved.name}`);
	await expect.poll(() => trail(page)).toEqual(["Home", movedName]);

	await page.getByRole("button", { name: "Entity actions" }).click();
	await page.getByRole("menuitem", { name: "Move" }).click();
	const dialog = page.getByRole("dialog");
	await dialog.getByText(destinationName, { exact: true }).click();
	await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().includes("suite.suite_drive.api.files.move") && response.ok(),
		),
		dialog.getByRole("button", { name: "Move", exact: true }).click(),
	]);

	await expect
		.poll(() => trail(page))
		.toEqual(["Home", destinationName, movedName]);
	await expect(page).toHaveURL(new RegExp(moved.name));
});
