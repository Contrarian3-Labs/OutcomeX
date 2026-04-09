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

        MockUSDCWithAuthorization usdc = new MockUSDCWithAuthorization();
        MockUSDT usdt = new MockUSDT();
        MockPermit2 permit2 = new MockPermit2();
        PWRToken pwr = new PWRToken(initialOwner);
        MachineAssetNFT machineAsset = new MachineAssetNFT(initialOwner);
        address[] memory supportedTokens = new address[](2);
        supportedTokens[0] = address(usdc);
        supportedTokens[1] = address(usdt);
        MachineMarketplace machineMarketplace = new MachineMarketplace(initialOwner, address(machineAsset), supportedTokens);
        RevenueVault revenueVault = new RevenueVault(initialOwner, address(pwr), address(machineAsset));
        SettlementController settlementController =
            new SettlementController(initialOwner, address(revenueVault), platformTreasury);
        OrderBook orderBook = new OrderBook(initialOwner, address(machineAsset));
        OrderPaymentRouter router = new OrderPaymentRouter(
            initialOwner,
            address(orderBook),
            address(usdc),
            address(usdt),
            address(pwr),
            address(permit2)
        );

        pwr.setMinter(address(revenueVault), true);
        pwr.setMinter(initialOwner, true);
        pwr.mint(initialOwner, 1_000_000 ether);
        pwr.setMinter(initialOwner, false);

        usdc.mint(initialOwner, 10_000_000 * 10 ** 6);
        usdt.mint(initialOwner, 10_000_000 * 10 ** 6);

        revenueVault.setSettlementController(address(settlementController));
        router.setSettlementEscrow(address(settlementController));
        settlementController.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlementController));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(address(router));
        machineAsset.setTransferGuard(address(orderBook));
        uint256 machineId = machineAsset.mintMachine(machineOwner, "ipfs://local-machine-001");

        vm.stopBroadcast();

        deployed = Deployment({
            usdc: address(usdc),
            usdt: address(usdt),
            permit2: address(permit2),
            pwr: address(pwr),
            machineAsset: address(machineAsset),
            machineMarketplace: address(machineMarketplace),
            revenueVault: address(revenueVault),
            settlementController: address(settlementController),
            orderBook: address(orderBook),
            orderPaymentRouter: address(router),
            sampleMachineId: machineId
        });

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
