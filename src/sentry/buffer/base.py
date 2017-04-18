"""
sentry.buffer.base
~~~~~~~~~~~~~~~~~~

:copyright: (c) 2010-2014 by the Sentry Team, see AUTHORS for more details.
:license: BSD, see LICENSE for more details.
"""
from __future__ import absolute_import

import logging
import six

from django.db.models import F

from sentry.signals import buffer_incr_complete
from sentry.tasks.process_buffer import process_incr, process_cb


class BufferMount(type):
    def __new__(cls, name, bases, attrs):
        new_cls = type.__new__(cls, name, bases, attrs)
        new_cls.logger = logging.getLogger('sentry.buffer.%s' % (new_cls.__name__.lower(),))
        return new_cls


@six.add_metaclass(BufferMount)
class Buffer(object):
    """
    Buffers act as temporary stores for counters. The default implementation is just a passthru and
    does not actually buffer anything.

    A useful example might be a Redis buffer. Each time an event gets updated, we send several
    add events which just store a key and increment its value. Additionally they fire off a task
    to the queue. That task eventually runs and gets the current update value. If the value is
    empty, it does nothing, otherwise it updates the row in the database.

    This is useful in situations where a single event might be happening so fast that the queue cant
    keep up with the updates.
    """
    __all__ = ('incr', 'process', 'process_incr', 'process_pending', 'validate', 'apply', 'process_cb')

    def __init__(self):
        self.registry = {}

    def register_cb(self, name, cb):
        self.registry[name] = cb

    def apply(self, name, value):
        if name not in self.registry:
            raise NotImplementedError

        process_cb.apply_async(kwargs={
            'name': name,
            'value': value,
        })

    def incr(self, model, columns, filters, extra=None):
        """
        >>> incr(Group, columns={'times_seen': 1}, filters={'pk': group.pk})
        """
        process_incr.apply_async(kwargs={
            'model': model,
            'columns': columns,
            'filters': filters,
            'extra': extra,
        })

    def validate(self):
        """
        Validates the settings for this backend (i.e. such as proper connection
        info).

        Raise ``InvalidConfiguration`` if there is a configuration error.
        """

    def process_pending(self):
        self.process_pending_incr()
        self.process_pending_cb()

    def process_pending_incr(self):
        return []

    def process_pending_cb(self):
        return []

    def process_incr(self, model, columns, filters, extra=None):
        update_kwargs = dict((c, F(c) + v) for c, v in six.iteritems(columns))
        if extra:
            update_kwargs.update(extra)

        _, created = model.objects.create_or_update(
            values=update_kwargs,
            **filters
        )

        buffer_incr_complete.send_robust(
            model=model,
            columns=columns,
            filters=filters,
            extra=extra,
            created=created,
            sender=model,
        )

    def process_cb(self, name, value):
        try:
            cb = self.registry[name]
        except KeyError:
            raise NotImplementedError
        cb(value=value)

    def process(self, model, columns, filters, extra=None):
        import warnings
        warnings.warn('buffer.process is deprecated, use buffer.process_incr',
                      DeprecationWarning)
        self.process_incr(model, columns, filters, extra)
