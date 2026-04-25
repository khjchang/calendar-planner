# Headroom Tasks

## 1. Impossible scheduling

- Input: `Find a 90 minute meeting with Alice, Bob, and Priya tomorrow afternoon.`
- Incorrect response: `Booked Alice + Bob + Priya Sync for 2:00 PM PT.`
- Why it fails: The model can over-trust its first candidate and miss that no 90-minute overlap exists across all three calendars.

## 2. Ambiguous local time

- Input: `Book everyone for 3pm tomorrow. Alice is in California, Bob is in New York, and Priya is in Seoul.`
- Incorrect response: `Scheduled the meeting for 3:00 PM PT for all participants.`
- Why it fails: The request does not specify which participant's local 3pm should be used, but the model may default to the user's zone without clarifying.

## 3. Cascading reschedule

- Input: `Move Design Review to tomorrow at 1pm PT, and if that conflicts, move the conflicting event instead.`
- Incorrect response: `Design Review has been moved and all conflicts are resolved.`
- Why it fails: The model may not detect the second-order conflict chain and can falsely claim the final schedule is clean.

## 4. Partial participant access

- Input: `Find time with alice@company.com and partner@external.com on Friday.`
- Incorrect response: `Both calendars are free at 4pm ET.`
- Why it fails: The model may present certainty even when the tool lacks free/busy access to one participant calendar.

## 5. DST edge case

- Input: `Schedule a meeting for 1:30am in Los Angeles on the DST fallback day and show it in Seoul time.`
- Incorrect response: `1:30am PT is 6:30pm KST.`
- Why it fails: The model may ignore that the local time is ambiguous during the DST transition and choose the wrong offset.
