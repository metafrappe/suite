import { expect, test } from "../../fixtures/test";
import { createFolder, driveEntities } from "../../helpers/drive";
import { frappeData } from "../../../shared/frappe";

function uniqueName(runId: string, label: string): string {
	return `${label}-${runId}-${Date.now()}`;
}

test("favourite and unfavourite a file toggles its Favourites membership", async ({
	owner,
	run,
}) => {
	await owner.page.goto("/drive");
	const name = uniqueName(run.run_id, "fav");
	const folder = await createFolder(owner.page, name);

	const favouriteNames = async (): Promise<string[]> => {
		const response = await owner.page.request.get(
			"/api/method/suite.suite_drive.api.list.favourites",
		);
		const rows = await frappeData<Array<{ name: string }>>(response);
		return rows.map((row) => row.name);
	};

	// Favourite it → appears in Favourites.
	const fav = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.set_favourite",
		{ data: { entities: [{ name: folder.name, is_favourite: true }] } },
	);
	expect(fav.ok()).toBe(true);
	await expect.poll(favouriteNames).toContain(folder.name);

	// Unfavourite it → gone from Favourites.
	const unfav = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.set_favourite",
		{ data: { entities: [{ name: folder.name, is_favourite: false }] } },
	);
	expect(unfav.ok()).toBe(true);
	await expect.poll(favouriteNames).not.toContain(folder.name);
});

test("search finds a newly created entity by name", async ({ owner, run }) => {
	await owner.page.goto("/drive");
	// A distinctive, index-friendly token (no hyphens, > 3 chars) so fulltext matches.
	const token = `zsearch${run.run_id.replace(/-/g, "")}${Date.now().toString(36)}`;
	const folder = await createFolder(owner.page, token);

	await expect
		.poll(async () => {
			const response = await owner.page.request.get(
				"/api/method/suite.suite_drive.api.files.search",
				{ params: { query: token } },
			);
			if (!response.ok()) return [];
			const rows = await frappeData<Array<{ name: string }>>(response).catch(
				() => [],
			);
			return Array.isArray(rows) ? rows.map((row) => row.name) : [];
		})
		.toContain(folder.name);
});
