import { describe, expect, it } from "vitest";
import { getErrorMessage } from "./error";

describe("getErrorMessage", () => {
	it("removes backend HTML from user-facing errors", () => {
		expect(
			getErrorMessage(
				new Error(
					"Login to access <strong>suite.suite_meet.api.meeting.join_meeting</strong>.",
				),
			),
		).toBe("Login to access suite.suite_meet.api.meeting.join_meeting.");
	});
});
