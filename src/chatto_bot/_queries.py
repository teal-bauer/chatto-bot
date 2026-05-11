"""Shared GraphQL fragments used by the WebSocket subscription, HTTP queries,
and mutations that return ``RoomEvent`` payloads.

Kept in one place so the data shape stays in lockstep across the three call
sites; mismatches were the cause of the original schema-drift incident.
"""

ROOM_EVENT_FRAGMENT = """\
fragment RoomEventFields on RoomEvent {
    id
    createdAt
    actorId
    actor { id login displayName avatarUrl presenceStatus }
    event {
        __typename
        ... on MessagePostedEvent {
            roomId body
            attachments {
                id roomId filename contentType width height url thumbnailUrl
                videoProcessing {
                    status durationMs width height thumbnailUrl errorMessage
                    variants { url quality width height size }
                }
            }
            linkPreview {
                url title description imageUrl siteName embedType embedId
            }
            inReplyTo inThread
            reactions { emoji count users { id login displayName } hasReacted }
            updatedAt replyCount lastReplyAt
            echoOfEventId echoFromThreadRootEventId
            threadParticipants(first: 5) {
                id login displayName avatarUrl presenceStatus
            }
            viewerIsFollowingThread
        }
        ... on MessageUpdatedEvent { roomId messageEventId }
        ... on MessageDeletedEvent { roomId messageEventId }
        ... on RoomCreatedEvent { roomId name description }
        ... on UserJoinedRoomEvent { roomId }
        ... on UserLeftRoomEvent { roomId }
        ... on RoomUpdatedEvent { roomId }
        ... on RoomDeletedEvent { roomId }
        ... on RoomArchivedEvent { roomId }
        ... on RoomUnarchivedEvent { roomId }
        ... on ReactionAddedEvent { roomId messageEventId emoji }
        ... on ReactionRemovedEvent { roomId messageEventId emoji }
        ... on UserTypingEvent { roomId threadRootEventId }
        ... on PresenceChangedEvent { status }
        ... on VideoProcessingCompletedEvent {
            roomId attachmentId messageEventId
        }
        ... on ServerMemberDeletedEvent { userId }
        ... on CallParticipantJoinedEvent { roomId }
        ... on CallParticipantLeftEvent { roomId }
    }
}
"""

SPACE_EVENT_FRAGMENT = ROOM_EVENT_FRAGMENT  # backward-compat alias
