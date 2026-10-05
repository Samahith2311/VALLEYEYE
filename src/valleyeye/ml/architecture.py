"""SNUNet-ECAM architecture from Orion-AI-Lab/KuroSiwo (MIT licensed)."""

from __future__ import annotations

from typing import cast

import torch
from torch import nn


class _NestedConv(nn.Module):
    def __init__(self, in_channels: int, mid_channels: int, out_channels: int) -> None:
        super().__init__()
        self.activation = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv2d(in_channels, mid_channels, 3, padding=1, bias=True)
        self.bn1 = nn.BatchNorm2d(mid_channels)
        self.conv2 = nn.Conv2d(mid_channels, out_channels, 3, padding=1, bias=True)
        self.bn2 = nn.BatchNorm2d(out_channels)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value = self.conv1(value)
        identity = value
        value = self.activation(self.bn1(value))
        value = self.bn2(self.conv2(value))
        return cast(torch.Tensor, self.activation(value + identity))


class _Up(nn.Module):
    def __init__(self, channels: int) -> None:
        super().__init__()
        self.up = nn.ConvTranspose2d(channels, channels, 2, stride=2)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return cast(torch.Tensor, self.up(value))


class _ChannelAttention(nn.Module):
    def __init__(self, channels: int, ratio: int = 16) -> None:
        super().__init__()
        hidden = channels // ratio
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        self.fc1 = nn.Conv2d(channels, hidden, 1, bias=False)
        self.relu1 = nn.ReLU()
        self.fc2 = nn.Conv2d(hidden, channels, 1, bias=False)
        self.sigmod = nn.Sigmoid()

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        average = self.fc2(self.relu1(self.fc1(self.avg_pool(value))))
        maximum = self.fc2(self.relu1(self.fc1(self.max_pool(value))))
        return cast(torch.Tensor, self.sigmod(average + maximum))


class SNUNetECAM(nn.Module):
    def __init__(self, in_channels: int = 3, out_channels: int = 3, base_channel: int = 32) -> None:
        super().__init__()
        n1 = base_channel
        filters = [n1, n1 * 2, n1 * 4, n1 * 8, n1 * 16]
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)

        self.conv0_0 = _NestedConv(in_channels, filters[0], filters[0])
        self.conv1_0 = _NestedConv(filters[0], filters[1], filters[1])
        self.Up1_0 = _Up(filters[1])
        self.conv2_0 = _NestedConv(filters[1], filters[2], filters[2])
        self.Up2_0 = _Up(filters[2])
        self.conv3_0 = _NestedConv(filters[2], filters[3], filters[3])
        self.Up3_0 = _Up(filters[3])
        self.conv4_0 = _NestedConv(filters[3], filters[4], filters[4])
        self.Up4_0 = _Up(filters[4])

        self.conv0_1 = _NestedConv(filters[0] * 2 + filters[1], filters[0], filters[0])
        self.conv1_1 = _NestedConv(filters[1] * 2 + filters[2], filters[1], filters[1])
        self.Up1_1 = _Up(filters[1])
        self.conv2_1 = _NestedConv(filters[2] * 2 + filters[3], filters[2], filters[2])
        self.Up2_1 = _Up(filters[2])
        self.conv3_1 = _NestedConv(filters[3] * 2 + filters[4], filters[3], filters[3])
        self.Up3_1 = _Up(filters[3])

        self.conv0_2 = _NestedConv(filters[0] * 3 + filters[1], filters[0], filters[0])
        self.conv1_2 = _NestedConv(filters[1] * 3 + filters[2], filters[1], filters[1])
        self.Up1_2 = _Up(filters[1])
        self.conv2_2 = _NestedConv(filters[2] * 3 + filters[3], filters[2], filters[2])
        self.Up2_2 = _Up(filters[2])

        self.conv0_3 = _NestedConv(filters[0] * 4 + filters[1], filters[0], filters[0])
        self.conv1_3 = _NestedConv(filters[1] * 4 + filters[2], filters[1], filters[1])
        self.Up1_3 = _Up(filters[1])
        self.conv0_4 = _NestedConv(filters[0] * 5 + filters[1], filters[0], filters[0])
        self.ca = _ChannelAttention(filters[0] * 4)
        self.ca1 = _ChannelAttention(filters[0], ratio=4)
        self.conv_final = nn.Conv2d(filters[0] * 4, out_channels, kernel_size=1)

        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(module, nn.BatchNorm2d | nn.GroupNorm):
                nn.init.constant_(module.weight, 1)
                nn.init.constant_(module.bias, 0)

    def forward(self, pre_event: torch.Tensor, post_event: torch.Tensor) -> torch.Tensor:
        x0_0a = self.conv0_0(pre_event)
        x1_0a = self.conv1_0(self.pool(x0_0a))
        x2_0a = self.conv2_0(self.pool(x1_0a))
        x3_0a = self.conv3_0(self.pool(x2_0a))

        x0_0b = self.conv0_0(post_event)
        x1_0b = self.conv1_0(self.pool(x0_0b))
        x2_0b = self.conv2_0(self.pool(x1_0b))
        x3_0b = self.conv3_0(self.pool(x2_0b))
        x4_0b = self.conv4_0(self.pool(x3_0b))

        x0_1 = self.conv0_1(torch.cat([x0_0a, x0_0b, self.Up1_0(x1_0b)], 1))
        x1_1 = self.conv1_1(torch.cat([x1_0a, x1_0b, self.Up2_0(x2_0b)], 1))
        x0_2 = self.conv0_2(torch.cat([x0_0a, x0_0b, x0_1, self.Up1_1(x1_1)], 1))
        x2_1 = self.conv2_1(torch.cat([x2_0a, x2_0b, self.Up3_0(x3_0b)], 1))
        x1_2 = self.conv1_2(torch.cat([x1_0a, x1_0b, x1_1, self.Up2_1(x2_1)], 1))
        x0_3 = self.conv0_3(torch.cat([x0_0a, x0_0b, x0_1, x0_2, self.Up1_2(x1_2)], 1))
        x3_1 = self.conv3_1(torch.cat([x3_0a, x3_0b, self.Up4_0(x4_0b)], 1))
        x2_2 = self.conv2_2(torch.cat([x2_0a, x2_0b, x2_1, self.Up3_1(x3_1)], 1))
        x1_3 = self.conv1_3(torch.cat([x1_0a, x1_0b, x1_1, x1_2, self.Up2_2(x2_2)], 1))
        x0_4 = self.conv0_4(torch.cat([x0_0a, x0_0b, x0_1, x0_2, x0_3, self.Up1_3(x1_3)], 1))

        combined = torch.cat([x0_1, x0_2, x0_3, x0_4], 1)
        intra = torch.stack([x0_1, x0_2, x0_3, x0_4]).sum(dim=0)
        attended = self.ca(combined) * (combined + self.ca1(intra).repeat(1, 4, 1, 1))
        return cast(torch.Tensor, self.conv_final(attended))
