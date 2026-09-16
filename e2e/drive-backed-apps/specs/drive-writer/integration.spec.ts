import { expect, test } from "../../fixtures/test";
import {
	createFolder,
	openEntityActions,
} from "../../helpers/drive";
import {
	createWriterDocument,
	openWriterDocument,
	uniqueWriterTitle,
	writerEditor,
} from "../../helpers/writer";
import { frappeData } from "../../../shared/frappe";

test("creating from Drive routes into Writer", async ({ owner }) => {
	await owner.page.goto("/drive/documents");
	await owner.page.locator("#create-button").click();
	await expect(owner.page).toHaveURL(/\/writer\/w\/[^/]+(?:\/|$)/);
	await expect(writerEditor(owner.page)).toBeVisible();
});

test("the same document is visible in Drive and Writer listings", async ({
	owner,
	run,
}) => {
	const title = uniqueWriterTitle(run.run_id, "cross-app");
	const file = await createWriterDocument(owner.page.request, title);

	await owner.page.goto("/drive/documents");
	const driveRow = owner.page.getByTestId(`drive-entity-${file.name}`);
	await expect(driveRow).toBeVisible();
	await expect(driveRow).toContainText(title);

	await owner.page.goto("/writer");
	const writerRow = owner.page.getByTestId(`writer-document-${file.name}`);
	await expect(writerRow).toBeVisible();
	await expect(writerRow).toContainText(title);
});

test("renames, moves, trashes, restores, and reopens a Writer document from Drive", async ({
	owner,
	run,
}) => {
	const title = uniqueWriterTitle(run.run_id, "lifecycle");
	const renamedTitle = `${title} renamed`;
	const folderName = `writer-folder-${run.run_id}-${Date.now()}`;
	const content = `Writer lifecycle content ${run.run_id}`;
	const embedContent = `Writer embed content ${run.run_id}`;
	const file = await createWriterDocument(owner.page.request, title);

	await openWriterDocument(owner.page, file.name);
	await writerEditor(owner.page).fill(content);
	await owner.page.keyboard.press("ControlOrMeta+s");
	await expect(owner.page.getByText("Saved document", { exact: true })).toBeVisible();
	const embedResponse = await owner.page.request.post(
		"/api/method/suite.writer.api.embed.add",
		{
			multipart: {
				file_id: file.name,
				file: {
					name: "writer-embed.txt",
					mimeType: "text/plain",
					buffer: Buffer.from(embedContent),
				},
			},
		},
	);
	const embed = await frappeData<{ file_url: string }>(embedResponse);

	await owner.page.goto("/drive");
	const folder = await createFolder(owner.page, folderName);
	const documentRow = owner.page.getByTestId(`drive-entity-${file.name}`);
	await expect(documentRow).toContainText(title);
	await openEntityActions(owner.page, file.name);
	await owner.page.getByRole("button", { name: "Rename", exact: true }).click();
	// Renaming is inline in the row, not a dialog.
	const renameInput = documentRow.getByRole("textbox");
	await renameInput.fill(renamedTitle);
	const [renameResponse] = await Promise.all([
		owner.page.waitForResponse(
			(response) => response.url().includes("suite.suite_drive.api.files.rename"),
		),
		renameInput.press("Enter"),
	]);
	if (!renameResponse.ok()) throw new Error(await renameResponse.text());
	await expect(documentRow).toContainText(renamedTitle);
	let fetchedEmbed = await owner.page.request.get(embed.file_url);
	expect(fetchedEmbed.ok()).toBe(true);
	expect(await fetchedEmbed.text()).toBe(embedContent);

	const moveResponse = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.move",
		{
			data: {
				entity_names: [file.name],
				new_parent: folder.name,
			},
		},
	);
	if (!moveResponse.ok()) throw new Error(await moveResponse.text());
	await owner.page.goto(`/drive/d/${folder.name}`);
	await expect(owner.page.getByTestId(`drive-entity-${file.name}`)).toContainText(
		renamedTitle,
	);
	fetchedEmbed = await owner.page.request.get(embed.file_url);
	expect(fetchedEmbed.ok()).toBe(true);
	expect(await fetchedEmbed.text()).toBe(embedContent);

	await openEntityActions(owner.page, file.name);
	await owner.page.getByRole("button", { name: "Delete", exact: true }).click();
	await owner.page
		.getByRole("dialog")
		.getByRole("button", { name: "Move to Trash" })
		.click();
	await owner.page.getByRole("link", { name: "Trash", exact: true }).click();
	await expect(owner.page.getByTestId(`drive-entity-${file.name}`)).toContainText(
		renamedTitle,
	);
	fetchedEmbed = await owner.page.request.get(embed.file_url);
	expect(fetchedEmbed.ok()).toBe(false);
	const restoreResponse = await owner.page.request.post(
		"/api/method/suite.suite_drive.api.files.remove_or_restore",
		{ data: { entity_names: [file.name] } },
	);
	if (!restoreResponse.ok()) throw new Error(await restoreResponse.text());
	fetchedEmbed = await owner.page.request.get(embed.file_url);
	expect(fetchedEmbed.ok()).toBe(true);
	expect(await fetchedEmbed.text()).toBe(embedContent);

	await owner.page.goto(`/drive/d/${folder.name}`);
	await owner.page.getByTestId(`drive-entity-${file.name}`).click();
	await expect(owner.page).toHaveURL(new RegExp(`/writer/w/${file.name}`));
	await expect(writerEditor(owner.page)).toContainText(content);
});
