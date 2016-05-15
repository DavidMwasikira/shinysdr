# Copyright 2016 Kevin Reid <kpreid@switchb.org>
#
# This file is part of ShinySDR.
# 
# ShinySDR is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# ShinySDR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with ShinySDR.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division

import json
import time

from twisted.internet import reactor as the_reactor  # TODO eliminate
from twisted.internet.protocol import ProcessProtocol
from twisted.protocols.basic import LineReceiver
from twisted.python import log
from zope.interface import implements

from gnuradio import blocks
from gnuradio import gr

from shinysdr.blocks import make_sink_to_process_stdin, test_subprocess
from shinysdr.filters import MultistageChannelFilter
from shinysdr.modes import ModeDef, IDemodulator
from shinysdr.signals import no_signal
from shinysdr.telemetry import ITelemetryMessage, ITelemetryObject
from shinysdr.types import Timestamp
from shinysdr.values import ExportedState, LooseCell, exported_value


drop_unheard_timeout_seconds = 120


class RTL433Demodulator(gr.hier_block2, ExportedState):
    implements(IDemodulator)
    
    def __init__(self, mode='433', input_rate=0, context=None):
        assert input_rate > 0
        assert context is not None
        gr.hier_block2.__init__(
            self, type(self).__name__,
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(0, 0, 0))
        
        # Note that the bandwidth chosen is not primarily determined by the bandwidth of the input signals, but by the frequency error of the transmitters.
        demod_rate = 250000  # rtl_433's default, presumably a good choice.
        self.__band_filter = MultistageChannelFilter(
            input_rate=input_rate,
            output_rate=demod_rate,
            cutoff_freq=demod_rate * 0.4,
            transition_width=demod_rate * 0.2)
        
        # Subprocess
        # using /usr/bin/env because twisted spawnProcess doesn't support path search
        # pylint: disable=no-member
        process = the_reactor.spawnProcess(
            RTL433ProcessProtocol(context.output_message),
            '/usr/bin/env',
            env=None,  # inherit environment
            args=['env', 'rtl_433',
                '-F', 'json',
                '-r', '-',  # read from stdin
                '-m', '3',  # complex float input
                '-s', str(demod_rate),
            ],
            childFDs={
                0: 'w',
                1: 'r',
                2: 2
            })
        sink = make_sink_to_process_stdin(process, itemsize=gr.sizeof_gr_complex)
        
        self.connect(
            self,
            self.__band_filter,
            blocks.multiply_const_cc(10.0),  # accounts for loss due to channel filtering I guess
            sink)
    
    def can_set_mode(self, mode):
        """implements IDemodulator"""
        return False
    
    @exported_value()
    def get_band_filter_shape(self):
        """implements IDemodulator"""
        return self.__band_filter.get_shape()
    
    def get_output_type(self):
        """implements IDemodulator"""
        return no_signal


class RTL433ProcessProtocol(ProcessProtocol):
    def __init__(self, target):
        self.__target = target
        self.__line_receiver = LineReceiver()
        self.__line_receiver.delimiter = '\n'
        self.__line_receiver.lineReceived = self.__lineReceived
    
    def outReceived(self, data):
        """Implements ProcessProtocol."""
        # split lines
        self.__line_receiver.dataReceived(data)
        
    def errReceived(self, data):
        """Implements ProcessProtocol."""
        # we should inherit stderr, not pipe it
        raise Exception('shouldn\'t happen')
    
    def __lineReceived(self, line):
        # rtl_433's JSON encoder is not perfect (e.g. it will emit unescaped newlines), so protect against parse failures
        try:
            message = json.loads(line)
        except ValueError:
            log.msg('bad JSON from rtl_433: %s' % line)
            return
        log.msg('rtl_433 message: %r' % (message,))
        # rtl_433 provides a time field, but when in file-input mode it assumes the input is not real-time and generates start-of-file-relative timestamps, so we can't use them.
        wrapper = RTL433MessageWrapper(message, time.time())
        self.__target(wrapper)


_message_field_is_id = {
    # common
    u'model': True,
    u'time': False,
    
    # id fields
    u'device': True,  # common
    u'channel': True,  # some
    u'id': True,  # some, frequenrly labeled 'house code'
    u'dev_id': True,  # one
    u'node': True,  # one
    u'address': True,  # one
    u'ws_id': True,  # one
    u'sid': True,  # one
    u'rid': True,  # one
    u'unit': True,  # one
    
    # data fields - device
    u'battery': False,
    u'rc': False,
    
    # data fields - weather
    u'temperature_F': False,
    u'temperature_C': False,
    u'temperature': False,
    u'humidity': False,
    u'wind_speed': False,
    u'wind_speed_ms': False,
    u'wind_gust': False,
    u'wind_gust_ms': False,
    u'wind_direction': False,
    u'direction': False,
    u'direction_str': False,
    u'direction_deg': False,
    u'speed': False,
    u'gust': False,
    u'rain': False,
    u'rain_total': False,
    u'rain_rate': False,
    u'rainfall_mm': False,
    u'total_rain': False,
    
    # data fields - other
    u'cmd': False,
    u'cmd_id': False,
    u'command': False,
    u'tristate': False,
    u'power0': False,
    u'power1': False,
    u'power2': False,
    u'ct1': False,
    u'ct2': False,
    u'ct3': False,
    u'ct4': False,
    u'Vrms/batt': False,
    u'pulse': False,
    u'temp1_C': False,
    u'temp2_C': False,
    u'temp3_C': False,
    u'temp4_C': False,
    u'temp5_C': False,
    u'temp6_C': False,
    u'msg_type': False,
    u'hours': False,
    u'minutes': False,
    u'seconds': False,
    u'year': False,
    u'month': False,
    u'day': False,
    u'button': False,
    u'button1': False,
    u'button2': False,
    u'button3': False,
    u'button4': False,
    u'group_call': False,
    u'dim': False,
    u'dim_value': False,
    u'maybetemp': False,
    u'flags': False,
    u'binding_countdown': False,
    u'depth': False,
    u'state': False,
}


class RTL433MessageWrapper(object):
    implements(ITelemetryMessage)
    
    def __init__(self, message, receive_time):
        self.message = message  # a parsed rtl_433 JSON-format message
        self.receive_time = float(receive_time)
        
        id_keys = [k for k in message if _message_field_is_id.get(k, False)]
        id_keys.sort()
        self.object_id = u'-'.join(unicode(message[k]) for k in id_keys)
    
    def get_object_id(self):
        return self.object_id
    
    def get_object_constructor(self):
        return RTL433MsgGroup


# TODO: It would make sense to make this a CollectionState object to have simple dynamic fields. But that is not possible because CollectionState is hardcoded to use block cells. Refactoring so "block cells" are not a thing is in the plan.
class RTL433MsgGroup(ExportedState):
    implements(ITelemetryObject)
    
    def __init__(self, object_id):
        """Implements ITelemetryObject."""
        self.__cells = {}
        self.__last_heard_time = None
    
    def state_is_dynamic(self):
        """Overrides ExportedState."""
        return True
    
    def state_def(self, callback):
        """Overrides ExportedState."""
        super(RTL433MsgGroup, self).state_def(callback)
        for cell in self.__cells.itervalues():
            callback(cell)
    
    # not exported
    def receive(self, message_wrapper):
        """Implements ITelemetryObject."""
        self.__last_heard_time = message_wrapper.receive_time
        for k, v in message_wrapper.message.iteritems():
            if _message_field_is_id.get(k, False) or k == u'time':
                continue
            if k not in self.__cells:
                self.__cells[k] = LooseCell(
                    key=k,
                    value=None,
                    type=object,
                    writable=False,
                    persists=False)
            self.__cells[k].set_internal(v)
    
    def is_interesting(self):
        """Implements ITelemetryObject."""
        return True
    
    def get_object_expiry(self):
        """implement ITelemetryObject"""
        return self.__last_heard_time + drop_unheard_timeout_seconds
    
    @exported_value(type=Timestamp())
    def get_last_heard_time(self):
        return self.__last_heard_time


# TODO: Arrange for a way for the user to see why it is unavailable.
_rtl_433_available = test_subprocess(
    ['rtl_433', '-r', '/dev/null'],
    'Reading samples from file',
    shell=False)


plugin_mode = ModeDef(
    mode='433',
    label='rtl_433',
    demod_class=RTL433Demodulator,
    available=_rtl_433_available)