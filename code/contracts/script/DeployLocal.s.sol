// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {MachineMarketplace} from "../src/MachineMarketplace.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {MockPermit2} from "../src/mocks/MockPermit2.sol";
import {MockUSDCWithAuthorization} from "../src/mocks/MockUSDCWithAuthorization.sol";
import {MockUSDT} from "../src/mocks/MockUSDT.sol";

interface Vm {
    function startBroadcast() external;
    function stopBroadcast() external;
}

contract DeployLocal {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    struct Deployment {
        address usdc;
        address usdt;
        address permit2;
        address pwr;
        address machineAsset;
        address machineMarketplace;
        address revenueVault;
        address settlementController;
        address orderBook;
        address orderPaymentRouter;
        uint256 sampleMachineId;
    }

    event DeploymentAddress(string name, address addr);
    event DeploymentMachineId(uint256 machineId);

    function run() external returns (Deployment memory deployed) {
        return _deploy(tx.origin, tx.origin, tx.origin);
    }

    function runWithConfig(address initialOwner, address platformTreasury, address machineOwner)
        external
        returns (Deployment memory deployed)
    {
        return _deploy(initialOwner, platformTreasury, machineOwner);
    }

    function _deploy(address initialOwner, address platformTreasury, address machineOwner)
        internal
        returns (Deployment memory deployed)
    {
        vm.startBroadcast();

        deployed.usdc = address(new MockUSDCWithAuthorization());
        deployed.usdt = address(new MockUSDT());
        deployed.permit2 = address(new MockPermit2());
        deployed.pwr = address(new PWRToken(initialOwner));
        deployed.machineAsset = address(new MachineAssetNFT(initialOwner));
        address[] memory supportedTokens = new address[](2);
        supportedTokens[0] = deployed.usdc;
        supportedTokens[1] = deployed.usdt;
        deployed.machineMarketplace = address(new MachineMarketplace(initialOwner, deployed.machineAsset, supportedTokens));
        deployed.revenueVault = address(new RevenueVault(initialOwner, deployed.pwr, deployed.machineAsset));
        deployed.settlementController =
            address(new SettlementController(initialOwner, deployed.revenueVault, platformTreasury));
        deployed.orderBook = address(new OrderBook(initialOwner, deployed.machineAsset));
        deployed.orderPaymentRouter = address(new OrderPaymentRouter(
            initialOwner,
            deployed.orderBook,
            deployed.usdc,
            deployed.usdt,
            deployed.pwr,
            deployed.permit2
        ));

        PWRToken(deployed.pwr).setMinter(deployed.revenueVault, true);
        PWRToken(deployed.pwr).setMinter(initialOwner, true);
        PWRToken(deployed.pwr).mint(initialOwner, 1_000_000 ether);
        PWRToken(deployed.pwr).setMinter(initialOwner, false);

        MockUSDCWithAuthorization(deployed.usdc).mint(initialOwner, 10_000_000 * 10 ** 6);
        MockUSDT(deployed.usdt).mint(initialOwner, 10_000_000 * 10 ** 6);

        RevenueVault(deployed.revenueVault).setSettlementController(deployed.settlementController);
        OrderPaymentRouter(deployed.orderPaymentRouter).setSettlementEscrow(deployed.settlementController);
        SettlementController(deployed.settlementController).setOrderBook(deployed.orderBook);
        OrderBook(deployed.orderBook).setSettlementController(deployed.settlementController);
        OrderBook(deployed.orderBook).setRevenueVault(deployed.revenueVault);
        OrderBook(deployed.orderBook).setPaymentAdapter(deployed.orderPaymentRouter);
        MachineAssetNFT(deployed.machineAsset).setTransferGuard(deployed.orderBook);
        deployed.sampleMachineId = MachineAssetNFT(deployed.machineAsset).mintMachine(machineOwner, "ipfs://local-machine-001");

        vm.stopBroadcast();

        emit DeploymentAddress("USDC", deployed.usdc);
        emit DeploymentAddress("USDT", deployed.usdt);
        emit DeploymentAddress("Permit2", deployed.permit2);
        emit DeploymentAddress("PWRToken", deployed.pwr);
        emit DeploymentAddress("MachineAssetNFT", deployed.machineAsset);
        emit DeploymentAddress("MachineMarketplace", deployed.machineMarketplace);
        emit DeploymentAddress("RevenueVault", deployed.revenueVault);
        emit DeploymentAddress("SettlementController", deployed.settlementController);
        emit DeploymentAddress("OrderBook", deployed.orderBook);
        emit DeploymentAddress("OrderPaymentRouter", deployed.orderPaymentRouter);
        emit DeploymentMachineId(deployed.sampleMachineId);
    }
}
