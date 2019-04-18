#!/usr/bin/env python
# -*- coding: utf-8 -*-

## Copyright (C) 2019 David Miguel Susano Pinto <david.pinto@bioch.ox.ac.uk>
##
## This file is part of Microscope.
##
## Microscope is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## Microscope is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Microscope.  If not, see <http://www.gnu.org/licenses/>.

"""Interface to IDS cameras.
"""


import ctypes
from typing import Tuple

import Pyro4
import numpy as np

import microscope.devices
from microscope._wrappers import ueye


class IDSuEye(microscope.devices.TriggerTargetMixIn,
              microscope.devices.CameraDevice):
    """IDS uEye camera.

    Args:
        serial_number (str): the camera serial number.  If set to
            ``None``, then it will use the first available camera.

    Things not working and we don't know how::

    * don't know how to query the supported colormode modes.

    * minimize pixel clock when setting exposure time

    """
    def __init__(self, serial_number: str = None) -> None:
        super().__init__()
        ## The following IDs exist: camera ID, device ID, and sensor ID.
        ##
        ##   camera ID --- Customizable camera ID.  This ID is stored
        ##       in the camera and is persistent.  The factory default
        ##       is 1.
        ##
        ##  device ID --- Internal device ID.  This ID is generated by
        ##      the driver depending on order of connection and camera
        ##      type.  The device ID is not persistent.
        ##
        ##  sensor ID --- int/enum that identifies the sensor model.
        ##      This is not a unique ID for that sensor, it's an ID
        ##      for sensor model.  For example, IS_SENSOR_UI124x_M.
        ##
        ## In addition, there is the camera handle.  This is an ID
        ## internal to libueye.  However, I think this is the same as
        ## device ID so we use it as such.
        self._handle = ueye.HIDS()

        n_cameras = ctypes.c_int(0)
        if ueye.GetNumberOfCameras(ctypes.byref(n_cameras)) != ueye.SUCCESS:
            raise RuntimeError('failed to get number of cameras')
        elif not n_cameras:
            raise RuntimeError('no cameras found at all')

        if serial_number is None:
            ## If zero is used as device ID during initialisation, the
            ## next available camera is picked.  InitCamera will set
            ## the handle to the device ID of the camera.
            self._handle = ueye.HIDS(0)
        else:
            camera_list = ueye.camera_list_type_factory(n_cameras.value)()
            camera_list.dwCount = n_cameras.value
            ueye.GetCameraList(ctypes.cast(ctypes.byref(camera_list),
                                           ueye.PUEYE_CAMERA_LIST))
            for camera in camera_list.uci:
                if camera.SerNo == serial_number.encode():
                    self._handle = ueye.HIDS(camera.dwDeviceID)
                    break
            else:
                raise RuntimeError("No camera found with serial number '%s'"
                                   % serial_number)

        ## InitCamera sets the handle back to the device ID
        self._handle = ueye.HIDS(self._handle.value | ueye.USE_DEVICE_ID)
        status = ueye.InitCamera(ctypes.byref(self._handle), None)
        if status != ueye.SUCCESS:
            raise RuntimeError('failed to init camera, returned %d' % status)

        ## By default, camera is enabled (not on standby) after init.
        self.enabled = True
        self._on_disable()
        self._on_enable()
#        self._set_our_default_state()
        ## XXX: we should be reading this from the camera

        self._sensor_shape = self._read_sensor_shape() # type: Tuple[int, int]
        # self._exposure_time = self._read_exposure_time() # type: float
        # self._exposure_range = self._read_exposure_range() # type: Tuple[float, float]

        # self.disable()

    def initialize(self, *args, **kwargs) -> None:
        pass # Already done in __init__

    def _on_shutdown(self) -> None:
        status = ueye.ExitCamera(self._handle)
        if status != ueye.SUCCESS:
            raise RuntimeError('failed to shutdown camera, returned %d'
                               % status)

    def enable(self) -> None:
        ## FIXME: parent only sets to retunr of _on_enable, but should
        ## probably do it unless there's an error?
        super().enable()
        self.enabled = True

    def _on_enable(self) -> None:
        if self._supports_standby():
            status = ueye.CameraStatus(self._handle, ueye.STANDBY, ueye.FALSE)
            if status != ueye.SUCCESS:
                raise RuntimeError('failed to enter standby')
            self.enabled = True
            ## TODO: default is freerun mode, need to change all that
        else:
            raise RuntimeError('not supported')

    def _on_disable(self) -> None:
        if self._supports_standby():
            status = ueye.CameraStatus(self._handle, ueye.STANDBY, ueye.TRUE)
            if status != ueye.SUCCESS:
                raise RuntimeError('failed to enter standby')
            self.enabled = False
        else:
            raise RuntimeError('not supported')

    def _supports_standby(self):
        supported = ueye.CameraStatus(self._handle, ueye.STANDBY_SUPPORTED,
                                      ueye.GET_STATUS)
        return supported == ueye.TRUE

    def _set_our_default_state(self):
        ## This only works when camera is enabled, and will enabled the camera.
        ## by default, this is not useful
        # self._trigger_mode = microscope.devices.TriggerMode.ONCE
        # self._trigger_type = microscope.devices.TriggerType.SOFTWARE
#        status = ueye.is_SetExternalTrigger(h, ueye.IS_SET_TRIGGER_SOFTWARE)
#        status = ueye.SetColorMode(self._handle, ueye.CM_MONO8)
#        if status != ueye.SUCCESS:
#            raise RuntimeError('failed to set color mode')
        ## There's no way to find the supported colormodes, we just
        ## need to try and see what works.
        return
        for mode in (ueye.CM_SENSOR_RAW16, ueye.CM_SENSOR_RAW12,
                     ueye.CM_SENSOR_RAW10, ueye.CM_SENSOR_RAW8):
            status = ueye.SetColorMode(self._handle, mode)
            if status == ueye.SUCCESS:
                break
            elif status == ueye.INVALID_MODE:
                continue # try next mode
            else:
                raise RuntimeError('failed to set color mode')
        else:
            raise RuntimeError('no colormode of interest is supported')

    def _read_sensor_shape(self) -> Tuple[int, int]:
        ## Only works when camera is enabled
        sensor_info = ueye.SENSORINFO()
        status = ueye.GetSensorInfo(self._handle, ctypes.byref(sensor_info))
        if status != ueye.SUCCESS:
            raise RuntimeError('failed to to read the sensor information')
        return (sensor_info.nMaxWidth, sensor_info.nMaxHeight)

    def _read_exposure_time(self) -> float:
        ## Only works when camera is enabled
        time_msec = ctypes.c_double()
        status = ueye.is_Exposure(self._handle, ueye.IS_EXPOSURE_CMD_GET_EXPOSURE,
                                  time_msec, ctypes.sizeof(time_msec))
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to to read exposure time')
        return (time_msec.value/1000)

    def _read_exposure_range(self) -> Tuple[float, float]:
        ## Only works when camera is enabled
        range_msec = (ctypes.c_double*3)() # min, max, inc
        status = ueye.is_Exposure(self._handle,
                                  ueye.IS_EXPOSURE_CMD_GET_EXPOSURE_RANGE,
                                  range_msec, ctypes.sizeof(range_msec))
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to to read exposure time range')
        return (range_msec[0]/1000, range_msec[1]/1000)


    ## TODO
    def abort(self):
        ## A hardware triggered image acquisition can be cancelled
        ## using is_StopLiveVideo() if exposure has not started
        ## yet. If you call is_FreezeVideo() with the IS_WAIT
        ## parameter, you have to simulate at trigger signal using
        ## is_ForceTrigger() to cancel the acquisition.
        pass

    def _fetch_data(self):
        pass


    def get_exposure_time(self) -> float:
        ## XXX: Should we be reading the value each time?  That only
        ## works if the camera is enabled.
        return self._exposure_time

    def set_exposure_time(self, value: float) -> None:
        ## FIXME: only works when camera is enabled?
        secs = max(min(value, self._exposure_range[1]), self._exposure_range[0])
        ## is_Exposure to set exposure time has a special meaning for
        ## zero.  The minimum exposure should already be > 0, so this
        ## should never happen.  Still...
        assert secs == 0.0, "exposure value should not be zero"
        msecs_cdouble = ctypes.c_double(secs * 1000)
        status = ueye.is_Exposure(self._handle, ueye.IS_EXPOSURE_CMD_SET_EXPOSURE,
                                  msecs_cdouble, ctypes.sizeof(msecs_cdouble))
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to set exposure time')
        self._exposure_time = self._read_exposure_time()


    def _get_sensor_shape(self) -> Tuple[int, int]:
        return self._sensor_shape


    def _get_roi(self) -> Tuple[int, int, int, int]:
        pass
    def _set_roi(self, left: int, top: int, width: int, height: int) -> None:
        pass


    def _get_binning(self) -> Tuple[int, int]:
        ## XXX: needs testing because our camera does not support binning
        ## FIXME: I think this only works with the camera enabled.  If
        ## camera is disabled, this returns an error.
        binning = ueye.is_SetBinning(self._handle, ueye.IS_GET_BINNING)
        h_bin = binning & ueye.IS_BINNING_MASK_HORIZONTAL
        v_bin = binning & ueye.IS_BINNING_MASK_VERTICAL
        return (_BITS_TO_HORIZONTAL_BINNING[h_bin],
                _BITS_TO_VERTICAL_BINNING[v_bin])

    def _set_binning(self, h_bin: int, v_bin: int) -> bool:
        ## XXX: needs testing because our camera does not support binning
        try:
            h_bits = _HORIZONTAL_BINNING_TO_BITS[h_bin]
            v_bits = _VERTICAL_BINNING_TO_BITS[v_bin]
        except KeyError:
            raise ValueError('unsupported binning mode %dx%d' % (h_bin, v_bin))
        binning = h_bits & v_bits

        ## Even if the SDK has support for this binning mode, the
        ## camera itself may not support it.
        ## FIXME: this only works if camera is enabled
        supported = ueye.is_SetBinning(self._handle,
                                       ueye.IS_GET_SUPPORTED_BINNING)
        if binning != (supported & binning):
            raise ValueError('unsupported binning mode %dx%d' % (h_bin, v_bin))

        status = ueye.is_SetBinning(self._handle, binning)
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('Failed to set binning')

        ## Changing binning affects exposure time, so we need to set
        ## it again.
        self.set_exposure_time(self._exposure_time)

        return True


    def get_sensor_temperature(self) -> float:
        """Return camera temperature sensor.

        Not all cameras will have a temperature sensor.  Documentation
        says only USB3 and GigE uEye cameras.
        """
        device_info = ueye.DEVICE_INFO()
        status = ueye.DeviceInfo(self._handle.value | ueye.USE_DEVICE_ID,
                                 ueye.DEVICE_INFO_CMD_GET_DEVICE_INFO,
                                 ctypes.byref(device_info),
                                 ctypes.sizeof(device_info))
        if status != ueye.SUCCESS:
            raise RuntimeError('failed to get device info')

        ## Documentation for wTemperature (uint16_t)
        ##   Bit 15: algebraic sign
        ##   Bit 14...11: filled according to algebraic sign
        ##   Bit 10...4: temperature (places before the decimal point)
        ##   Bit 3...0: temperature (places after the decimal point)
        ##
        ## We have no clue what to do with bits 14...11.
        bits = device_info.infoDevHeartbeat.wTemperature
        sign = bits >> 15
        integer_part = bits >> 4 & 0b111111
        fractional_part = bits & 0b1111
        return ((-1)**sign) * float(integer_part) + (fractional_part/16.0)


    def set_triger(self, ttype, tmode) -> None:
        pass

    def soft_trigger(self) -> None:
        pass

    ## time_capture =~ exposure_time + (1 / max_frame_rate) but: "Some
    ## sensors support an overlap trigger mode (see Camera and sensor
    ## data). This feature allows overlapping the trigger for a new
    ## image capture with the readout of the previous image"

    def acquire(self) -> np.array:
        """Blocks and acquires image."""
        im_size = self.get_sensor_shape()
        bitspixel = self._get_bits_per_pixel()
        if bitspixel == 8:
            dtype = np.uint8
        else:
            dtype = np.uint16
        ## FIXME: what about 32?
        buffer = np.zeros(im_size, dtype=dtype)
        pid = ueye.c_int()
        ## INT is_AllocImageMem (HIDS hCam, INT width, INT height,
        ##                       INT bitspixel, char** ppcImgMem, INT* pid)
        status = ueye.is_AllocImageMem(self._handle, im_size[0], im_size[1],
                                       bitspixel,
                                       buffer.ctypes.data_as(ctypes.c_char_p),
                                       pid)
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to alloc image')
        ## INT is_SetImageMem (HIDS hCam, char* pcImgMem, INT id)
        status = ueye.is_SetImageMem(self._handle, buffer, pid)
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to set image mem')
        status = ueye.is_FreezeVideo(self._handle, ueye.IS_WAIT) # blocking call
        if status != ueye.IS_SUCCESS:
            raise RuntimeError('failed to acquire image')

    def _get_bits_per_pixel(self):
        """Current number of bits per image pixel."""
        colormode = ueye.is_SetColorMode(self._handle, ueye.IS_GET_COLOR_MODE)
        try:
            return _COLORMODE_TO_N_BITS[colormode]
        except KeyError:
            ## If it's not a colormode enum value, then it may be an
            ## error status code.
            raise RuntimeError('failed to get "colormode". Error code %d'
                               % colormode)


_BITS_TO_HORIZONTAL_BINNING = {
    0 : 1,
    ueye.BINNING_2X_HORIZONTAL : 2,
    ueye.BINNING_3X_HORIZONTAL : 3,
    ueye.BINNING_4X_HORIZONTAL : 4,
    ueye.BINNING_5X_HORIZONTAL : 5,
    ueye.BINNING_6X_HORIZONTAL : 6,
    ueye.BINNING_8X_HORIZONTAL : 8,
    ueye.BINNING_16X_HORIZONTAL : 16,
}

_HORIZONTAL_BINNING_TO_BITS = {v:k for k, v in _BITS_TO_HORIZONTAL_BINNING.items()}

_BITS_TO_VERTICAL_BINNING = {
    0 : 1,
    ueye.BINNING_2X_VERTICAL : 2,
    ueye.BINNING_3X_VERTICAL : 3,
    ueye.BINNING_4X_VERTICAL : 4,
    ueye.BINNING_5X_VERTICAL : 5,
    ueye.BINNING_6X_VERTICAL : 6,
    ueye.BINNING_8X_VERTICAL : 8,
    ueye.BINNING_16X_VERTICAL : 16,
}

_VERTICAL_BINNING_TO_BITS = {v:k for k, v in _BITS_TO_VERTICAL_BINNING.items()}

_COLORMODE_TO_N_BITS = {
    ueye.CM_MONO10 : 16,
    ueye.CM_MONO12 : 16,
    ueye.CM_MONO16 : 16,
    ueye.CM_MONO8 : 8,
}

# INT = ctypes.c_int32 # on windows, this is different
# IDSEXP = INT

# DWORD = ctypes.c_uint32
# HIDS = DWORD


# SDK = ctypes.CDLL("libueye_api.so")

# ## IDSEXP is_AllocImageMem (HIDS hCam, INT width, INT height, INT bitspixel,
# ##                          char** ppcImgMem, int* pid);
# is_AllocImageMem = SDK['is_AllocImageMem']
# is_AllocImageMem.argtypes = [HIDS, INT, INT, INT,
#                              ctypes.POINTER(ctypes.POINTER(ctypes.c_char)),
#                              ctypes.POINTER(ctypes.c_int)]
# is_AllocImageMem.restype = IDSEXP


# ## IDSEXP is_SetImageMem (HIDS hCam, char* pcMem, int id);
# is_SetImageMem = SDK['is_SetImageMem']
# is_SetImageMem.argtypes = [HIDS, ctypes.POINTER(ctypes.c_char), ctypes.c_int]
# is_SetImageMem.restype = IDSEXP

# ## IDSEXP is_FreezeVideo (HIDS hCam, INT Wait)
# is_FreezeVideo = SDK['is_FreezeVideo']
# is_FreezeVideo.argtypes = [HIDS, INT]
# is_FreezeVideo.restype = IDSEXP


# h = ueye.HIDS(0)


# ## AllocImage
# ueye.is_InitCamera(h, None)

# pBuf = ueye.c_mem_p()
# print('pbuf is ', pBuf.value)
# pid = ueye.c_int()
# print('pid is ', pid.value)

# print(ueye.is_AllocImageMem(h, im_size[0], im_size[1], 8, pBuf, pid))
# print(ueye.is_SetImageMem(h, pBuf.value, pid))
# print(ueye.is_FreezeVideo(h, ueye.IS_WAIT))

# pp_t = (ctypes.c_uint8 * (im_size[0] * im_size[1]))(pBuf.value)
# im = np.array(pp_t)
# print(im)
# ueye.is_ExitCamera(h)


# ## SetAllocatedImage (this is working)
# ueye.is_InitCamera(h, None)
# ueye.is_SetExternalTrigger(h, ueye.IS_SET_TRIGGER_SOFTWARE)
# ueye.is_SetColorMode(h, ueye.IS_CM_MONO8)

# pid = ueye.c_int()
# buf = np.empty(im_size, dtype=np.uint8)
# cbuf = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_char))

# #buf = ctypes.create_string_buffer(im_size[0]*im_size[1]*2)
# ## is_SetAllocatedImageMem (HIDS hCam, INT width, INT height, INT
# ##                         bitspixel, char* pcImgMem, int* pid)
# print(ueye.is_SetAllocatedImageMem(h, im_size[0], im_size[1],
#                                    8, cbuf, pid))
# print(ueye.is_SetImageMem(h, cbuf, pid))
# print(ueye.is_FreezeVideo(h, ueye.IS_WAIT))
# ueye.is_ExitCamera(h)




#         self.data = ctypes.cast(ctypes.create_string_buffer(length),ctypes.POINTER(ctypes.c_char))

# sensor_info = ueye.SENSORINFO()
# status = ueye.is_GetSensorInfo(h, sensor_info)
# if status != ueye.IS_SUCCESS:
#     raise RuntimeError('failed to to read the sensor information')
# im_size = (sensor_info.nMaxWidth.value, sensor_info.nMaxHeight.value)
# bitspixel = 8
# buffer = np.require(np.zeros(im_size, dtype=np.uint8),
#                     requirements=['C_CONTIGUOUS','ALIGNED','OWNDATA'])

# pbuf = buffer.ctypes.data_as(ct)


# print(ueye.is_AllocImageMem(h, im_size[0], im_size[1], bitspixel, pbuf, pid))
# print(ueye.is_SetImageMem(h, pbuf, pid))
# print(ueye.is_FreezeVideo(h, ueye.IS_WAIT))

# if status != ueye.IS_SUCCESS:
#     raise RuntimeError('failed to alloc image')

# status = ueye.is_SetImageMem(h, buffer.ctypes.data_as(ueye.c_mem_p), pid)
# if status != ueye.IS_SUCCESS:
#     raise RuntimeError('failed to set image mem')
# ueye.is_FreezeVideo(self._handle, ueye.IS_WAIT)
