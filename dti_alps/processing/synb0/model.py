"""
UNet3D neural network architecture for synB0-DISCO.

This model takes 2 input channels (b0 and T1 in atlas space) and produces
1 output channel (synthetic distortion-free b0).

Architecture based on Schilling et al., "Synthesized b0 for diffusion
distortion correction (Synb0-DISCO)", Magnetic Resonance Imaging, 2019.
"""

import torch
import torch.nn as nn


class UNet3D(nn.Module):
    """
    3D U-Net architecture for synthesizing distortion-free b0 images.

    Input shape: (batch, 2, 80, 96, 80) - b0 and T1 in atlas space
    Output shape: (batch, 1, 80, 96, 80) - synthetic distortion-free b0
    """

    def __init__(self, n_in: int = 2, n_out: int = 1):
        """
        Initialize UNet3D.

        Parameters
        ----------
        n_in : int
            Number of input channels (default: 2 for b0 + T1)
        n_out : int
            Number of output channels (default: 1 for synthetic b0)
        """
        super().__init__()

        # Encoder
        self.ec0 = self._encoder_block(n_in, 32, kernel_size=3, stride=1, padding=1)
        self.ec1 = self._encoder_block(32, 64, kernel_size=3, stride=1, padding=1)
        self.pool0 = nn.MaxPool3d(2)
        self.ec2 = self._encoder_block(64, 64, kernel_size=3, stride=1, padding=1)
        self.ec3 = self._encoder_block(64, 128, kernel_size=3, stride=1, padding=1)
        self.pool1 = nn.MaxPool3d(2)
        self.ec4 = self._encoder_block(128, 128, kernel_size=3, stride=1, padding=1)
        self.ec5 = self._encoder_block(128, 256, kernel_size=3, stride=1, padding=1)
        self.pool2 = nn.MaxPool3d(2)
        self.ec6 = self._encoder_block(256, 256, kernel_size=3, stride=1, padding=1)
        self.ec7 = self._encoder_block(256, 512, kernel_size=3, stride=1, padding=1)
        self.el = nn.Conv3d(512, 512, kernel_size=1, stride=1, padding=0)

        # Decoder
        self.dc9 = self._decoder_block(512, 512, kernel_size=2, stride=2, padding=0)
        self.dc8 = self._decoder_block(512 + 256, 256, kernel_size=3, stride=1, padding=1)
        self.dc7 = self._decoder_block(256, 256, kernel_size=3, stride=1, padding=1)
        self.dc6 = self._decoder_block(256, 256, kernel_size=2, stride=2, padding=0)
        self.dc5 = self._decoder_block(256 + 128, 128, kernel_size=3, stride=1, padding=1)
        self.dc4 = self._decoder_block(128, 128, kernel_size=3, stride=1, padding=1)
        self.dc3 = self._decoder_block(128, 128, kernel_size=2, stride=2, padding=0)
        self.dc2 = self._decoder_block(128 + 64, 64, kernel_size=3, stride=1, padding=1)
        self.dc1 = self._decoder_block(64, 64, kernel_size=3, stride=1, padding=1)
        self.dc0 = self._decoder_block(64, n_out, kernel_size=1, stride=1, padding=0)
        self.dl = nn.ConvTranspose3d(n_out, n_out, kernel_size=1, stride=1, padding=0)

    def _encoder_block(
        self, in_channels: int, out_channels: int, kernel_size: int, stride: int, padding: int
    ) -> nn.Sequential:
        """Create an encoder block with Conv3D + InstanceNorm + LeakyReLU."""
        return nn.Sequential(
            nn.Conv3d(
                in_channels,
                out_channels,
                kernel_size,
                stride=stride,
                padding=padding,
                bias=False,
            ),
            nn.InstanceNorm3d(out_channels),
            nn.LeakyReLU(),
        )

    def _decoder_block(
        self, in_channels: int, out_channels: int, kernel_size: int, stride: int, padding: int
    ) -> nn.Sequential:
        """Create a decoder block with ConvTranspose3D + InstanceNorm + LeakyReLU."""
        return nn.Sequential(
            nn.ConvTranspose3d(
                in_channels,
                out_channels,
                kernel_size,
                stride=stride,
                padding=padding,
                bias=False,
            ),
            nn.InstanceNorm3d(out_channels),
            nn.LeakyReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through the network.

        Parameters
        ----------
        x : torch.Tensor
            Input tensor of shape (batch, 2, 80, 96, 80)

        Returns
        -------
        torch.Tensor
            Output tensor of shape (batch, 1, 80, 96, 80)
        """
        # Encode
        e0 = self.ec0(x)
        syn0 = self.ec1(e0)
        del e0

        e1 = self.pool0(syn0)
        e2 = self.ec2(e1)
        syn1 = self.ec3(e2)
        del e1, e2

        e3 = self.pool1(syn1)
        e4 = self.ec4(e3)
        syn2 = self.ec5(e4)
        del e3, e4

        e5 = self.pool2(syn2)
        e6 = self.ec6(e5)
        e7 = self.ec7(e6)

        # Last layer without relu
        el = self.el(e7)
        del e5, e6, e7

        # Decode
        d9 = torch.cat((self.dc9(el), syn2), 1)
        del el, syn2

        d8 = self.dc8(d9)
        d7 = self.dc7(d8)
        del d9, d8

        d6 = torch.cat((self.dc6(d7), syn1), 1)
        del d7, syn1

        d5 = self.dc5(d6)
        d4 = self.dc4(d5)
        del d6, d5

        d3 = torch.cat((self.dc3(d4), syn0), 1)
        del d4, syn0

        d2 = self.dc2(d3)
        d1 = self.dc1(d2)
        del d3, d2

        d0 = self.dc0(d1)
        del d1

        # Last layer without relu
        out = self.dl(d0)

        return out
