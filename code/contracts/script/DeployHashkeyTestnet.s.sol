// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {MachineMarketplace} from "../src/MachineMarketplace.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";

interface Vm {
    function startBroadcast() external;
    function stopBroadcast() external;
}

contract DeployHashkeyTestnet {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    struct Deployment {
        address usdc;
        address usdt;
        address pwr;
        address machineAsset;
        address machineMarketplace;
        address revenueVault;
        address settlementController;
        address orderBook;
        address orderPaymentRouter;
        uint256 sampleMachineIdOwner1;
        uint256 sampleMachineIdOwner2;
    }

    event DeploymentAddress(string name, address addr);
    event DeploymentMachineId(string label, uint256 machineId);

    function runWithConfig(
        address initialOwner,
        address platformTreasury,
        address machineOwner1,
        address machineOwner2,
        address usdcAddress,
        address usdtAddress
    ) external returns (Deployment memory deployed) {
        require(initialOwner != address(0), "ZERO_INITIAL_OWNER");
        require(platformTreasury != address(0), "ZERO_PLATFORM_TREASURY");
        require(machineOwner1 != address(0), "ZERO_MACHINE_OWNER_1");
        require(machineOwner2 != address(0), "ZERO_MACHINE_OWNER_2");
        require(usdcAddress != address(0), "ZERO_USDC");
        require(usdtAddress != address(0), "ZERO_USDT");

        vm.startBroadcast();

        deployed.usdc = usdcAddress;
        deployed.usdt = usdtAddress;
        deployed.pwr = address(new PWRToken(initialOwner));
        deployed.machineAsset = address(new MachineAssetNFT(initialOwner));

        address[] memory supportedTokens = new address[](2);
        supportedTokens[0] = deployed.usdc;
        supportedTokens[1] = deployed.usdt;

        deployed.machineMarketplace = address(new MachineMarketplace(initialOwner, deployed.machineAsset, supportedTokens));
        deployed.revenueVault = address(new RevenueVault(initialOwner, deployed.pwr, deployed.machineAsset, 25));
        deployed.settlementController =
            address(new SettlementController(initialOwner, deployed.revenueVault, deployed.pwr, platformTreasury));
        deployed.orderBook = address(new OrderBook(initialOwner, deployed.machineAsset));
        deployed.orderPaymentRouter = address(
            new OrderPaymentRouter(
                initialOwner,
                deployed.orderBook,
                deployed.usdc,
                deployed.usdt,
                deployed.pwr
            )
        );

        PWRToken(deployed.pwr).setMinter(deployed.revenueVault, true);
        PWRToken(deployed.pwr).setMinter(initialOwner, true);
        PWRToken(deployed.pwr).mint(initialOwner, 1_000_000 ether);
        PWRToken(deployed.pwr).setMinter(initialOwner, false);

        RevenueVault(deployed.revenueVault).setSettlementController(deployed.settlementController);
        OrderPaymentRouter(deployed.orderPaymentRouter).setSettlementEscrow(deployed.settlementController);
        SettlementController(deployed.settlementController).setOrderBook(deployed.orderBook);
        OrderBook(deployed.orderBook).setSettlementController(deployed.settlementController);
        OrderBook(deployed.orderBook).setRevenueVault(deployed.revenueVault);
        OrderBook(deployed.orderBook).setPaymentAdapter(deployed.orderPaymentRouter);
        MachineAssetNFT(deployed.machineAsset).setTransferGuard(deployed.orderBook);

        deployed.sampleMachineIdOwner1 =
            MachineAssetNFT(deployed.machineAsset).mintMachine(machineOwner1, "ipfs://hashkey-testnet-machine-owner-1");
        deployed.sampleMachineIdOwner2 =
            MachineAssetNFT(deployed.machineAsset).mintMachine(machineOwner2, "ipfs://hashkey-testnet-machine-owner-2");

        vm.stopBroadcast();

        emit DeploymentAddress("USDC", deployed.usdc);
        emit DeploymentAddress("USDT", deployed.usdt);
        emit DeploymentAddress("PWRToken", deployed.pwr);
        emit DeploymentAddress("MachineAssetNFT", deployed.machineAsset);
        emit DeploymentAddress("MachineMarketplace", deployed.machineMarketplace);
        emit DeploymentAddress("RevenueVault", deployed.revenueVault);
        emit DeploymentAddress("SettlementController", deployed.settlementController);
        emit DeploymentAddress("OrderBook", deployed.orderBook);
        emit DeploymentAddress("OrderPaymentRouter", deployed.orderPaymentRouter);
        emit DeploymentMachineId("owner-1", deployed.sampleMachineIdOwner1);
        emit DeploymentMachineId("owner-2", deployed.sampleMachineIdOwner2);
    }
}
