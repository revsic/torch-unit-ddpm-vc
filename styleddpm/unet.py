from typing import List

import torch
import torch.nn as nn

from .resblock import AuxResidualBlock, ModulatedAuxResidualBlock


class AuxSequential(nn.Module):
    """Sequential wrapper for auxiliary input passing.
    """
    def __init__(self, lists: List[nn.Module]):
        """Initializer.
        Args:
            lists: module lists.
        """
        super().__init__()
        self.lists = nn.ModuleList(lists)

    def forward(self, inputs: torch.Tensor, *aux) -> torch.Tensor:
        """Chaining outputs with auxiliary inputs.
        """
        x = inputs
        for module in self.lists:
            x = module(x, *aux)
        return x


class UNet(nn.Module):
    """Spectrogram U-Net for noise estimator.
    """
    def __init__(self,
                 mel: int,
                 channels: int,
                 kernels: int,
                 longrange: int,
                 aux: int,
                 styles: int,
                 stages: int,
                 blocks: int):
        """Initializer.
        Args:
            mel: size of the mel filter channels.
            channels: size of the hidden channels.
            kernels: size of the convolutional kernels.
            longrange: size of the long-range kernels for masked context smoothing.
            aux: size of the auxiliary channels.
            styles: size of the style vectors.
            stages: the number of the resolution scales.
            blocks: the number of the residual blocks in each stages.
        """
        super().__init__()
        self.proj = nn.Conv1d(mel, channels, 1)
        self.smoother = nn.Conv1d(
            mel, mel, longrange, groups=mel, padding=longrange // 2, bias=False)
        self.dblocks = nn.ModuleList([
            AuxSequential([
                AuxResidualBlock(channels * 2 ** i, kernels, aux, mel)
                for _ in range(blocks)])
            for i in range(stages - 1)])

        self.downsamples = nn.ModuleList([
            # half resolution
            nn.Conv1d(channels * 2 ** i, channels * 2 ** (i + 1), kernels, 2, padding=kernels // 2)
            for i in range(stages - 1)])

        self.neck = ModulatedAuxResidualBlock(
            channels * 2 ** (stages - 1), kernels, aux, styles)

        self.upsamples = nn.ModuleList([
            # double resolution
            nn.Sequential(
                nn.Upsample(scale_factor=2, mode='nearest'),
                nn.Conv1d(channels * 2 ** i, channels * 2 ** (i - 1), kernels, padding=kernels // 2))
            for i in range(stages - 1, 0, -1)])

        self.ublocks = nn.ModuleList([
            AuxSequential([
                ModulatedAuxResidualBlock(channels * 2 ** i, kernels, aux, styles)
                for _ in range(blocks)])
            for i in range(stages - 2, -1, -1)])

        self.proj_out = nn.Sequential(
            nn.ReLU(),
            nn.Conv1d(channels, mel, 1))

    def forward(self,
                inputs: torch.Tensor,
                aux: torch.Tensor,
                styles: torch.Tensor,
                context: torch.Tensor) -> torch.Tensor:
        """Spectrogram U-net.
        Args:
            inputs: [torch.float32; [B, mel, T]], input tensor, spectrogram.
            aux: [torch.float32; [B, A(=aux)]], auxiliary informations, times.
            styles: [torch.float32; [B, styles]], style vectors.
            context: [torch.float32; [B, mel, T]], contextualized vectors.
        Returns:
            [torch.float32; [B, mel, T]], transformed.
        """
        # [B, C, T]
        x = self.proj(inputs)
        # [B, mel, T]
        context = self.smoother(context)
        # (stages - 1) x [B, C x 2^i, T / 2^i]
        internals = []
        for dblock, downsample in zip(self.dblocks, self.downsamples):
            # [B, C x 2^i, T / 2^i]
            x = dblock(x, aux, context)
            internals.append(x)
            # [B, C x 2^(i + 1), T / 2^(i + 1)]
            x = downsample(x)
        # [B, C x 2^stages, T / 2^stages]
        x = self.neck(x, aux, styles)
        for i, ublock, upsample in zip(reversed(internals), self.ublocks, self.upsamples):
            # [B, C x 2^i, T / 2^i]
            x = ublock(upsample(x) + i, aux, styles)
        # [B, mel, T]
        return self.proj_out(x)


if __name__ == '__main__':
    # Test for U-net
    def test():
        BSIZE = 2
        MEL = 16
        TIMESTEP = 32
        AUX = 8
        STYLES = 4
        unet = UNet(
            mel=MEL,
            channels=32,
            kernels=3,
            longrange=11,
            aux=AUX,
            styles=STYLES,
            stages=5,
            blocks=2)
        inputs = torch.randn(BSIZE, MEL, TIMESTEP)
        aux = torch.randn(BSIZE, AUX)
        styles = torch.randn(BSIZE, STYLES)
        context = torch.randn(BSIZE, MEL, TIMESTEP)
        assert unet(inputs, aux, styles, context).shape == inputs.shape
        
        print('success')

    test()
