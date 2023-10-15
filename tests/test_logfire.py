import json
import re
from dataclasses import dataclass
from typing import cast

import pytest
from dirty_equals import IsPositive, IsStr
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from pydantic import BaseModel
from pydantic_core import ValidationError

import logfire
from logfire import LogfireSpan
from logfire._constants import (
    ATTRIBUTES_LOG_LEVEL_KEY,
    ATTRIBUTES_MESSAGE_KEY,
    ATTRIBUTES_MESSAGE_TEMPLATE_KEY,
    ATTRIBUTES_SPAN_TYPE_KEY,
    ATTRIBUTES_TAGS_KEY,
    NULL_ARGS_KEY,
)
from logfire.config import configure
from logfire.testing import IncrementalIdGenerator, TestExporter, TimeGenerator


def test_span_without_kwargs(exporter: TestExporter) -> None:
    with pytest.raises(KeyError, match="'name'"):
        with logfire.span('test {name}', span_name='test span'):
            pass  # pragma: no cover


def test_span_with_kwargs(exporter: TestExporter) -> None:
    with logfire.span('test {name=} {number}', span_name='test span', name='foo', number=3, extra='extra') as s:
        pass

    assert s.name == 'test span'
    assert s.parent is None
    assert s.start_time is not None
    assert s.end_time is not None
    assert s.start_time < s.end_time
    assert len(s.events) == 0

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test span (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_kwargs',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'test span',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_kwargs',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_span_with_parent(exporter: TestExporter) -> None:
    with logfire.span('{type} span', span_name='test parent span', type='parent') as p:
        with logfire.span('{type} span', span_name='test child span', type='child') as c:
            pass

    assert p.name == 'test parent span'
    assert p.parent is None
    assert len(p.events) == 0
    assert p.attributes is not None
    assert ATTRIBUTES_TAGS_KEY not in p.attributes

    assert c.name == 'test child span'
    assert c.parent == p.context
    assert len(c.events) == 0
    assert c.attributes is not None
    assert ATTRIBUTES_TAGS_KEY not in c.attributes

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test parent span (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_parent',
                'type': 'parent',
                'logfire.msg_template': '{type} span',
                'logfire.msg': 'parent span',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'test child span (start)',
            'context': {'trace_id': 1, 'span_id': 4, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'start_time': 2000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_parent',
                'type': 'child',
                'logfire.msg_template': '{type} span',
                'logfire.msg': 'child span',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '1',
            },
        },
        {
            'name': 'test child span',
            'context': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 2000000000,
            'end_time': 3000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_parent',
                'type': 'child',
                'logfire.msg_template': '{type} span',
                'logfire.msg': 'child span',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'test parent span',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 4000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_parent',
                'type': 'parent',
                'logfire.msg_template': '{type} span',
                'logfire.msg': 'parent span',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_span_with_tags(exporter: TestExporter) -> None:
    with logfire.tags('tag1', 'tag2').span(
        'test {name} {number}', span_name='test span', name='foo', number=3, extra='extra'
    ) as s:
        pass

    assert s.name == 'test span'
    assert s.parent is None
    assert s.start_time is not None and s.end_time is not None
    assert s.start_time < s.end_time
    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_TAGS_KEY] == ('tag1', 'tag2')
    assert len(s.events) == 0

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test span (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_tags',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'test {name} {number}',
                'logfire.msg': 'test foo 3',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'test span',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_with_tags',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'test {name} {number}',
                'logfire.msg': 'test foo 3',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_span_without_span_name(exporter: TestExporter) -> None:
    with logfire.span('test {name=} {number}', name='foo', number=3, extra='extra') as s:
        pass

    assert s.name == 'test {name=} {number}'
    assert s.parent is None
    assert s.start_time is not None and s.end_time is not None
    assert s.start_time < s.end_time
    assert len(s.events) == 0
    assert s.attributes is not None
    assert ATTRIBUTES_TAGS_KEY not in s.attributes
    assert s.attributes[ATTRIBUTES_MESSAGE_KEY] == 'test name=foo 3'
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test {name=} {number}'

    assert len(exporter.exported_spans) == 2
    # # because both spans have been ended

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test {name=} {number} (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_without_span_name',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'test {name=} {number}',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_without_span_name',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_span_use_span_name_in_formatting(exporter: TestExporter) -> None:
    with logfire.span('test {name=} {number} {span_name}', span_name='bar', name='foo', number=3, extra='extra') as s:
        pass

    assert isinstance(s, LogfireSpan)
    assert s.name == 'bar'
    assert s.parent is None
    assert s.start_time is not None and s.end_time is not None
    assert s.start_time < s.end_time
    assert len(s.events) == 0
    assert s.attributes is not None
    assert ATTRIBUTES_TAGS_KEY not in s.attributes
    assert s.attributes[ATTRIBUTES_MESSAGE_KEY] == 'test name=foo 3 bar'
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test {name=} {number} {span_name}'

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'bar (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_use_span_name_in_formatting',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number} {span_name}',
                'logfire.msg': 'test name=foo 3 bar',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'bar',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_use_span_name_in_formatting',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number} {span_name}',
                'logfire.msg': 'test name=foo 3 bar',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_span_end_on_exit_false(exporter: TestExporter) -> None:
    with logfire.span('test {name=} {number}', name='foo', number=3, extra='extra') as s:
        s.end_on_exit = False

    assert s.name == 'test {name=} {number}'
    assert s.parent is None
    assert s.end_time is None
    assert isinstance(s.start_time, int)
    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_MESSAGE_KEY] == 'test name=foo 3'
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test {name=} {number}'

    assert len(exporter.exported_spans) == 1
    span = exporter.exported_spans[0]
    assert span.attributes is not None
    assert span.attributes[ATTRIBUTES_SPAN_TYPE_KEY] == 'start_span'
    # because the real span hasn't ended yet

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test {name=} {number} (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_end_on_exit_false',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        }
    ]

    with s.activate(end_on_exit=True):
        pass

    assert isinstance(s.end_time, int)
    assert s.end_time > s.start_time
    assert len(exporter.exported_spans) == 2
    span = exporter.exported_spans[1]
    assert span.attributes is not None
    assert span.attributes[ATTRIBUTES_SPAN_TYPE_KEY] == 'span'

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test {name=} {number} (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_end_on_exit_false',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'test {name=} {number}',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_span_end_on_exit_false',
                'name': 'foo',
                'number': 3,
                'extra': 'extra',
                'logfire.msg_template': 'test {name=} {number}',
                'logfire.msg': 'test name=foo 3',
                'logfire.span_type': 'span',
            },
        },
    ]


@pytest.mark.parametrize('level', ('critical', 'debug', 'error', 'info', 'notice', 'warning'))
def test_log(exporter: TestExporter, level: str):
    getattr(logfire, level)('test {name} {number} {none}', name='foo', number=2, none=None)

    s = exporter.exported_spans[0]

    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_LOG_LEVEL_KEY] == level
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test {name} {number} {none}'
    assert s.attributes[ATTRIBUTES_MESSAGE_KEY] == 'test foo 2 null'
    assert s.attributes[ATTRIBUTES_SPAN_TYPE_KEY] == 'log'
    assert s.attributes['name'] == 'foo'
    assert s.attributes['number'] == 2
    assert s.attributes[NULL_ARGS_KEY] == ('none',)
    assert ATTRIBUTES_TAGS_KEY not in s.attributes

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test foo 2 null',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': level,
                'logfire.msg_template': 'test {name} {number} {none}',
                'logfire.msg': 'test foo 2 null',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_log',
                'name': 'foo',
                'number': 2,
                'logfire.null_args': ('none',),
            },
        }
    ]


def test_log_equals(exporter: TestExporter) -> None:
    logfire.info('test message {foo=} {bar=}', foo='foo', bar=3)

    s = exporter.exported_spans[0]

    assert s.name == 'test message foo=foo bar=3'
    assert s.attributes is not None
    assert s.attributes['foo'] == 'foo'
    assert s.attributes['bar'] == 3
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test message {foo=} {bar=}'
    assert s.attributes[ATTRIBUTES_LOG_LEVEL_KEY] == 'info'
    assert s.attributes[ATTRIBUTES_SPAN_TYPE_KEY] == 'log'

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test message foo=foo bar=3',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test message {foo=} {bar=}',
                'logfire.msg': 'test message foo=foo bar=3',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_log_equals',
                'foo': 'foo',
                'bar': 3,
            },
        }
    ]


def test_log_with_tags(exporter: TestExporter):
    logfire.tags('tag1', 'tag2').info('test {name} {number}', name='foo', number=2)

    s = exporter.exported_spans[0]

    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] == 'test {name} {number}'
    assert s.attributes[ATTRIBUTES_SPAN_TYPE_KEY] == 'log'
    assert s.attributes['name'] == 'foo'
    assert s.attributes['number'] == 2
    assert s.attributes[ATTRIBUTES_TAGS_KEY] == ('tag1', 'tag2')

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'test foo 2',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test {name} {number}',
                'logfire.msg': 'test foo 2',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_log_with_tags',
                'name': 'foo',
                'number': 2,
                'logfire.tags': ('tag1', 'tag2'),
            },
        }
    ]


def test_log_with_multiple_tags(exporter: TestExporter):
    logfire_with_2_tags = logfire.tags('tag1').tags('tag2')
    logfire_with_2_tags.info('test {name} {number}', name='foo', number=2)
    assert len(exporter.exported_spans) == 1
    s = exporter.exported_spans[0]
    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_TAGS_KEY] == ('tag1', 'tag2')

    logfire_with_4_tags = logfire_with_2_tags.tags('tag3', 'tag4')
    logfire_with_4_tags.info('test {name} {number}', name='foo', number=2)
    assert len(exporter.exported_spans) == 2
    s = exporter.exported_spans[1]
    assert s.attributes is not None
    assert s.attributes[ATTRIBUTES_TAGS_KEY] == ('tag1', 'tag2', 'tag3', 'tag4')


def test_instrument(exporter: TestExporter):
    @logfire.instrument('hello-world {a=}')
    def hello_world(a: int) -> str:
        return f'hello {a}'

    assert hello_world(123) == 'hello 123'

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'tests.test_logfire.test_instrument.<locals>.hello_world (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': '_main.py',
                'code.lineno': 123,
                'code.function': '_instrument_wrapper',
                'a': 123,
                'logfire.msg_template': 'hello-world {a=}',
                'logfire.msg': 'hello-world a=123',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'tests.test_logfire.test_instrument.<locals>.hello_world',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': '_main.py',
                'code.lineno': 123,
                'code.function': '_instrument_wrapper',
                'a': 123,
                'logfire.msg_template': 'hello-world {a=}',
                'logfire.msg': 'hello-world a=123',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_instrument_extract_false(exporter: TestExporter):
    @logfire.instrument('hello-world', extract_args=False)
    def hello_world(a: int) -> str:
        return f'hello {a}'

    assert hello_world(123) == 'hello 123'

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'tests.test_logfire.test_instrument_extract_false.<locals>.hello_world (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': '_main.py',
                'code.lineno': 123,
                'code.function': '_instrument_wrapper',
                'logfire.msg_template': 'hello-world',
                'logfire.msg': 'hello-world',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'tests.test_logfire.test_instrument_extract_false.<locals>.hello_world',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': '_main.py',
                'code.lineno': 123,
                'code.function': '_instrument_wrapper',
                'logfire.msg_template': 'hello-world',
                'logfire.msg': 'hello-world',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_validation_error_on_instrument(exporter: TestExporter):
    class Model(BaseModel, plugin_settings={'logfire': 'disable'}):
        a: int

    @logfire.instrument('hello-world {a=}')
    def run(a: str) -> Model:
        return Model(a=a)  # type: ignore

    with pytest.raises(ValidationError):
        run('haha')

    s = exporter.exported_spans.pop()
    assert len(s.events) == 1
    event = s.events[0]
    assert event.name == 'exception' and event.attributes
    assert event.attributes.get('exception.type') == 'ValidationError'
    assert '1 validation error for Model' in cast(str, event.attributes.get('exception.message'))
    assert event.attributes.get('exception.stacktrace') is not None

    data = json.loads(cast(str, event.attributes.get('exception.logfire.data')))
    # insert_assert(data)
    assert data == [
        {
            'type': 'int_parsing',
            'loc': ['a'],
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'haha',
        }
    ]

    errors = json.loads(cast(str, event.attributes.get('exception.logfire.trace')))
    # insert_assert(errors)
    assert errors == {
        'stacks': [
            {
                'exc_type': 'ValidationError',
                'exc_value': IsStr(
                    regex=(
                        re.escape(
                            "1 validation error for Model\n"
                            "a\n"
                            "  Input should be a valid integer, unable to parse string as an integer "
                            "[type=int_parsing, input_value='haha', input_type=str]\n"
                        )
                        + r'    For further information visit https://errors\.pydantic\.dev/[\d\.]+/v/int_parsing'
                    ),
                    regex_flags=re.MULTILINE,
                ),
                'syntax_error': None,
                'is_cause': False,
                'frames': [
                    {
                        'filename': IsStr(regex=r'.*/logfire/_main.py'),
                        'lineno': IsPositive(),
                        'name': 'activate',
                        'line': '',
                        'locals': None,
                    },
                    {
                        'filename': IsStr(regex=r'.*/tests/test_logfire.py'),
                        'lineno': IsPositive(),
                        'name': 'run',
                        'line': '',
                        'locals': None,
                    },
                    {
                        'filename': IsStr(regex=r'.*/pydantic/main.py'),
                        'lineno': IsPositive(),
                        'name': '__init__',
                        'line': '',
                        'locals': None,
                    },
                ],
            }
        ]
    }


def test_validation_error_on_span(exporter: TestExporter) -> None:
    class Model(BaseModel, plugin_settings={'logfire': 'disable'}):
        a: int

    def run(a: str) -> None:
        with logfire.span('test', span_name='test span'):
            Model(a=a)  # type: ignore

    with pytest.raises(ValidationError):
        run('haha')

    s = exporter.exported_spans.pop()
    assert len(s.events) == 1
    event = s.events[0]
    assert event.name == 'exception' and event.attributes
    assert event.attributes.get('exception.type') == 'ValidationError'
    assert '1 validation error for Model' in cast(str, event.attributes.get('exception.message'))
    assert event.attributes.get('exception.stacktrace') is not None

    data = json.loads(cast(bytes, event.attributes.get('exception.logfire.data')))
    # insert_assert(data)
    assert data == [
        {
            'type': 'int_parsing',
            'loc': ['a'],
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'haha',
        }
    ]

    errors = json.loads(cast(bytes, event.attributes.get('exception.logfire.trace')))
    # insert_assert(errors)
    print(errors)
    assert errors == {
        'stacks': [
            {
                'exc_type': 'ValidationError',
                'exc_value': IsStr(
                    regex=(
                        re.escape(
                            "1 validation error for Model\n"
                            "a\n"
                            "  Input should be a valid integer, unable to parse string as an integer "
                            "[type=int_parsing, input_value='haha', input_type=str]\n"
                        )
                        + r'    For further information visit https://errors\.pydantic\.dev/[\d\.]+/v/int_parsing'
                    ),
                    regex_flags=re.MULTILINE,
                ),
                'syntax_error': None,
                'is_cause': False,
                'frames': [
                    {
                        'filename': IsStr(regex=r'.*/logfire/_main.py'),
                        'lineno': IsPositive(),
                        'name': 'activate',
                        'line': '',
                        'locals': None,
                    },
                    {
                        'filename': IsStr(regex=r'.*/tests/test_logfire.py'),
                        'lineno': IsPositive(),
                        'name': 'run',
                        'line': '',
                        'locals': None,
                    },
                    {
                        'filename': IsStr(regex=r'.*/pydantic/main.py'),
                        'lineno': IsPositive(),
                        'name': '__init__',
                        'line': '',
                        'locals': None,
                    },
                ],
            }
        ]
    }


@dataclass
class Foo:
    x: int
    y: int


def test_json_args(exporter: TestExporter) -> None:
    logfire.info('test message {foo=}', foo=Foo(1, 2))
    logfire.info('test message {foos=}', foos=[Foo(1, 2)])

    assert len(exporter.exported_spans) == 2
    s = exporter.exported_spans[0]
    assert s.name == 'test message foo=Foo(x=1, y=2)'
    assert s.attributes is not None
    assert s.attributes['foo__JSON'] == '{"$__datatype__":"dataclass","data":{"x":1,"y":2},"cls":"Foo"}'

    s = exporter.exported_spans[1]
    assert s.name == 'test message foos=[Foo(x=1, y=2)]'
    assert s.attributes is not None
    assert s.attributes['foos__JSON'] == '[{"$__datatype__":"dataclass","data":{"x":1,"y":2},"cls":"Foo"}]'


def test_propagate_config_to_tags() -> None:
    time_generator = TimeGenerator()
    exporter = TestExporter()

    tags1 = logfire.tags('tag1', 'tag2')

    configure(
        send_to_logfire=False,
        console_print='off',
        ns_timestamp_generator=time_generator,
        id_generator=IncrementalIdGenerator(),
        processors=[SimpleSpanProcessor(exporter)],
    )

    tags2 = logfire.tags('tag3', 'tag4')

    for lf in (logfire, tags1, tags2):
        with lf.span('root'):
            with lf.span('child'):
                logfire.info('test1')
                tags1.info('test2')
                tags2.info('test3')

    # insert_assert(exporter.exported_spans_as_dict())
    assert exporter.exported_spans_as_dict() == [
        {
            'name': 'root (start)',
            'context': {'trace_id': 1, 'span_id': 2, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 1000000000,
            'end_time': 1000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'child (start)',
            'context': {'trace_id': 1, 'span_id': 4, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'start_time': 2000000000,
            'end_time': 2000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '1',
            },
        },
        {
            'name': 'test1',
            'context': {'trace_id': 1, 'span_id': 5, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'start_time': 3000000000,
            'end_time': 3000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test1',
                'logfire.msg': 'test1',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
            },
        },
        {
            'name': 'test2',
            'context': {'trace_id': 1, 'span_id': 6, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'start_time': 4000000000,
            'end_time': 4000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test2',
                'logfire.msg': 'test2',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
            },
        },
        {
            'name': 'test3',
            'context': {'trace_id': 1, 'span_id': 7, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'start_time': 5000000000,
            'end_time': 5000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test3',
                'logfire.msg': 'test3',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
            },
        },
        {
            'name': 'child',
            'context': {'trace_id': 1, 'span_id': 3, 'is_remote': False},
            'parent': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'start_time': 2000000000,
            'end_time': 6000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'root',
            'context': {'trace_id': 1, 'span_id': 1, 'is_remote': False},
            'parent': None,
            'start_time': 1000000000,
            'end_time': 7000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'root (start)',
            'context': {'trace_id': 2, 'span_id': 9, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 8, 'is_remote': False},
            'start_time': 8000000000,
            'end_time': 8000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'child (start)',
            'context': {'trace_id': 2, 'span_id': 11, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 10, 'is_remote': False},
            'start_time': 9000000000,
            'end_time': 9000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '8',
            },
        },
        {
            'name': 'test1',
            'context': {'trace_id': 2, 'span_id': 12, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 10, 'is_remote': False},
            'start_time': 10000000000,
            'end_time': 10000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test1',
                'logfire.msg': 'test1',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
            },
        },
        {
            'name': 'test2',
            'context': {'trace_id': 2, 'span_id': 13, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 10, 'is_remote': False},
            'start_time': 11000000000,
            'end_time': 11000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test2',
                'logfire.msg': 'test2',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
            },
        },
        {
            'name': 'test3',
            'context': {'trace_id': 2, 'span_id': 14, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 10, 'is_remote': False},
            'start_time': 12000000000,
            'end_time': 12000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test3',
                'logfire.msg': 'test3',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
            },
        },
        {
            'name': 'child',
            'context': {'trace_id': 2, 'span_id': 10, 'is_remote': False},
            'parent': {'trace_id': 2, 'span_id': 8, 'is_remote': False},
            'start_time': 9000000000,
            'end_time': 13000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'root',
            'context': {'trace_id': 2, 'span_id': 8, 'is_remote': False},
            'parent': None,
            'start_time': 8000000000,
            'end_time': 14000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'root (start)',
            'context': {'trace_id': 3, 'span_id': 16, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 15, 'is_remote': False},
            'start_time': 15000000000,
            'end_time': 15000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '0',
            },
        },
        {
            'name': 'child (start)',
            'context': {'trace_id': 3, 'span_id': 18, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 17, 'is_remote': False},
            'start_time': 16000000000,
            'end_time': 16000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'start_span',
                'logfire.start_parent_id': '15',
            },
        },
        {
            'name': 'test1',
            'context': {'trace_id': 3, 'span_id': 19, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 17, 'is_remote': False},
            'start_time': 17000000000,
            'end_time': 17000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test1',
                'logfire.msg': 'test1',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
            },
        },
        {
            'name': 'test2',
            'context': {'trace_id': 3, 'span_id': 20, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 17, 'is_remote': False},
            'start_time': 18000000000,
            'end_time': 18000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test2',
                'logfire.msg': 'test2',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag1', 'tag2'),
            },
        },
        {
            'name': 'test3',
            'context': {'trace_id': 3, 'span_id': 21, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 17, 'is_remote': False},
            'start_time': 19000000000,
            'end_time': 19000000000,
            'attributes': {
                'logfire.span_type': 'log',
                'logfire.level': 'info',
                'logfire.msg_template': 'test3',
                'logfire.msg': 'test3',
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
            },
        },
        {
            'name': 'child',
            'context': {'trace_id': 3, 'span_id': 17, 'is_remote': False},
            'parent': {'trace_id': 3, 'span_id': 15, 'is_remote': False},
            'start_time': 16000000000,
            'end_time': 20000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
                'logfire.msg_template': 'child',
                'logfire.msg': 'child',
                'logfire.span_type': 'span',
            },
        },
        {
            'name': 'root',
            'context': {'trace_id': 3, 'span_id': 15, 'is_remote': False},
            'parent': None,
            'start_time': 15000000000,
            'end_time': 21000000000,
            'attributes': {
                'code.filepath': 'test_logfire.py',
                'code.lineno': 123,
                'code.function': 'test_propagate_config_to_tags',
                'logfire.tags': ('tag3', 'tag4'),
                'logfire.msg_template': 'root',
                'logfire.msg': 'root',
                'logfire.span_type': 'span',
            },
        },
    ]


def test_int_span_id_encoding():
    """https://github.com/pydantic/platform/pull/388"""

    AnyValue(int_value=2**63 - 1)
    with pytest.raises(ValueError, match='Value out of range: 9223372036854775808'):
        AnyValue(int_value=2**63)
    AnyValue(string_value=str(2**63 - 1))
    AnyValue(string_value=str(2**63))
    AnyValue(string_value=str(2**128))
