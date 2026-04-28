"""Shared GraphQL fragments used by the WebSocket subscription, HTTP queries,
and mutations that return ``SpaceEvent`` payloads.

Kept in one place so the data shape stays in lockstep across the three call
sites; mismatches were the cause of the original schema-drift incident.
"""

SPACE_EVENT_FRAGMENT = """\
fragment SpaceEventFields on SpaceEvent {
    id
    createdAt
    actorId
    actor { id login displayName avatarUrl presenceStatus }
    event {
        __typename
        ... on MessagePostedEvent {
            roomId body
            attachments {
                id spaceId filename contentType width height url thumbnailUrl
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
        ... on UserJoinedRoomEvent { spaceId roomId }
        ... on UserLeftRoomEvent { spaceId roomId }
        ... on RoomUpdatedEvent { roomId }
        ... on RoomDeletedEvent { roomId }
        ... on RoomArchivedEvent { roomId }
        ... on RoomUnarchivedEvent { roomId }
        ... on ReactionAddedEvent { spaceId roomId messageEventId emoji }
        ... on ReactionRemovedEvent { spaceId roomId messageEventId emoji }
        ... on UserTypingEvent { spaceId roomId threadRootEventId }
        ... on PresenceChangedEvent { status }
        ... on VideoProcessingCompletedEvent {
            spaceId roomId attachmentId messageEventId
        }
        ... on SpaceMemberDeletedEvent { spaceId userId }
        ... on CallParticipantJoinedEvent { spaceId roomId }
        ... on CallParticipantLeftEvent { spaceId roomId }
    }
}
"""


# InstanceEvent doesn't get a fragment here because its wrapper type name is
# not known to us (the web client doesn't use a fragment either) and it's
# only used in one place (the subscription).
