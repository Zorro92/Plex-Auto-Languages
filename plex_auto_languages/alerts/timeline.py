from __future__ import annotations
from typing import TYPE_CHECKING
from datetime import datetime, timedelta
from plexapi.video import Episode

from plex_auto_languages.alerts.base import PlexAlert
from plex_auto_languages.utils.logger import get_logger
from plex_auto_languages.constants import EventType

if TYPE_CHECKING:
    from plex_auto_languages.plex_server import PlexServer


logger = get_logger()


class PlexTimeline(PlexAlert):

    TYPE = "timeline"

    @property
    def has_metadata_state(self):
        return "metadataState" in self._message

    @property
    def has_media_state(self):
        return "mediaState" in self._message

    @property
    def item_id(self):
        return int(self._message.get("itemID", 0))

    @property
    def identifier(self):
        return self._message.get("identifier", None)

    @property
    def state(self):
        return self._message.get("state", None)

    @property
    def entry_type(self):
        return self._message.get("type", None)

    def process(self, plex: PlexServer):
        try:
            # Early return checks
            if self.has_metadata_state or self.has_media_state:
                return
            if self.identifier != "com.plexapp.plugins.library" or self.state != 5 or self.entry_type == -1:
                return

            # Skip if not an Episode
            item = plex.fetch_item(self.item_id)
            if item is None or not isinstance(item, Episode):
                return

            # Skip if the show should be ignored
            try:
                show = item.show()
                if plex.should_ignore_show(show):
                    logger.debug(f"[Timeline] Ignoring episode {item} due to Plex show labels")
                    return
            except Exception as e:
                logger.debug(f"[Timeline] Error checking show for episode {item}: {str(e)}")
                return

            # Check if the item has a valid timestamp
            added_at = getattr(item, 'addedAt', None)
            if added_at is None:
                logger.debug(f"[Timeline] Episode {item} has no addedAt timestamp, treating as new")
            else:
                # Check if the item was added recently (within last 5 minutes)
                if added_at < datetime.now() - timedelta(minutes=5):
                    logger.debug(f"[Timeline] Episode {item} was added more than 5 minutes ago, skipping")
                    return

                # Check if the item has already been processed
                if not plex.cache.should_process_recently_added(item.key, added_at):
                    logger.debug(f"[Timeline] Episode {item} has already been processed")
                    return

            # Change tracks for all users
            logger.info(f"[Timeline] Processing newly added episode {plex.get_episode_short_name(item)}")
            plex.process_new_or_updated_episode(self.item_id, EventType.NEW_EPISODE, True)

        except Exception as e:
            logger.error(f"[Timeline] Error processing timeline alert: {str(e)}")
