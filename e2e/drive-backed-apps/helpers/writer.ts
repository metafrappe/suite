import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { frappeData } from "../../shared/frappe";

export interface WriterFile {
	name: string;
	file_name: string;
	content_docname: string;
}

export function uniqueWriterTitle(runId: string, scenario: string): string {
	return `E2E Writer ${scenario} ${runId} ${Date.now().toString(36)}`;
}

export async function createWriterDocument(
	request: APIRequestContext,
	title: string,
): Promise<WriterFile> {
	const response = await request.post(
		"/api/method/suite.writer.api.docs.create_document",
		{ form: { title } },
	);
	return frappeData<WriterFile>(response);
}

export async function shareWriterDocument(
	request: APIRequestContext,
	entityName: string,
	options: { user?: string; read: boolean; write?: boolean; comment?: boolean },
): Promise<void> {
	const response = await request.post(
		"/api/method/suite.suite_drive.api.files.update_access",
		{
			form: {
				entity_name: entityName,
				method: "share",
				user: options.user ?? "",
				read: options.read ? 1 : 0,
				write: options.write ? 1 : 0,
				comment: options.comment ? 1 : 0,
			},
		},
	);
	if (!response.ok()) {
		throw new Error(`Sharing Writer document failed: ${await response.text()}`);
	}
}

export async function openWriterDocument(page: Page, id: string): Promise<void> {
	await page.goto(`/writer/w/${id}`);
	await expect(writerEditor(page)).toBeVisible();
}

export function writerEditor(page: Page) {
	return page.getByRole("textbox", { name: "Document editor" });
}
